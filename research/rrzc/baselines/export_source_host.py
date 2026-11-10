"""Temporary system-only guest: export matching source, archive, shut down, reclaim."""
import argparse
import getpass
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
RRZC = HERE.parent
sys.path[:0] = [str(RRZC), str(RRZC / 'tests')]
from run_calibration_host import CalibrationRun, check_baseline_budget, detach_supervisor
from run_rr_path_host import preflight, IMAGE_DIR
from run_all_blocks_followup_host import PARENT
from run_joint_host import OLD_DEVICE
from result_storage import configure, compact, atomic_json
from ycsb_af import digest, require


class SourceRun(CalibrationRun):
    def command(self, argv, timeout=60):
        if argv and str(argv[0]).endswith('qemu-system-x86_64'):
            require(argv.count(OLD_DEVICE) == 1, 'unexpected inherited device arguments')
            index = argv.index(OLD_DEVICE)
            require(argv[index - 1] == '-device', 'unexpected device option')
            argv = list(argv)
            del argv[index - 1:index + 1]
            memory_gib = getattr(self, 'system_memory_gib', 2)
            argv[argv.index('-m') + 1] = str(memory_gib) + 'G'
            argv[argv.index('-smp') + 1] = '2'
            def unlocked():
                resource.setrlimit(resource.RLIMIT_MEMLOCK, (0, 0))
            result = subprocess.run(argv, cwd=RRZC.parents[1], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, preexec_fn=unlocked)
            with (self.output / '系统客机启动.log').open('x') as stream:
                stream.write(json.dumps(argv, ensure_ascii=False) + '\n' + result.stdout)
            require(result.returncode == 0, 'system-only guest start failed')
            self.record(模拟SSD接入=False,内存预算字节=memory_gib << 30)
            return result.stdout
        return super().command(argv, timeout)


def execute(args, password, output):
    run = SourceRun(args, password, output, 'source', 8101, PARENT, retain_image=False)
    run.fresh_image = True
    configure(run, ftl_audit=False)
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='仅软件源码导出，不接模拟SSD或保存NAND状态')
    atomic_json(output / '结果保存策略.json', policy)
    run.record(结果保存策略=policy, 边界='只读源码导出，2GiB系统客机，无设备实验')
    error = None
    try:
        run.start()
        run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
        run.deployed = True
        source = HERE / 'export_source_guest.py'
        run.command(run.scp() + [str(source), f'femu@127.0.0.1:{run.target}/'])
        require(run.ssh('sha256sum ' + run.target + '/export_source_guest.py').split()[0] == digest(source),
                'export helper identity mismatch')
        run.record(阶段='只读核验完整源码与原库并导出')
        run.ssh(f'python3 {run.target}/export_source_guest.py {run.target}', 300)
        run.record(阶段='源码导出完成，归档并正常关机')
    except BaseException as failure:
        error = failure
        run.record(原始错误=repr(failure))
        raise
    finally:
        run.finish(error)
    local = output / args.label
    proof = json.loads((local / 'export_complete.json').read_text())
    require(proof['passed'] and digest(local / 'source.tar.gz') == proof['source_tar_sha256'],
            'exported source archive identity mismatch')
    run.record(阶段='软件源码已归档，系统客机已正常退出',源码文件数=proof['source_file_count'])
    compact(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='joint-related-source-v1-20261106')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    require(args.label.startswith('joint-related-source-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label), 'invalid label')
    budget = preflight()  # Preserve the existing memory/disk resource protection thresholds.
    budget['目录已占字节'] = check_baseline_budget(1 << 30)
    budget['新增磁盘预算字节'] = 1 << 30
    output = IMAGE_DIR / (args.label + '-results')
    require(not output.exists(), 'do not overwrite source evidence')
    if args.check:
        print(json.dumps(budget, ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('源码导出客机认证（仅内存）：')
    output.mkdir()
    (output / '预注册计划.json').write_text(json.dumps(dict(
        用途='导出当前RocksDB完整源码与构建身份',父镜像=str(PARENT),资源=budget,
        模拟SSD接入=False,系统内存字节=2 << 30,修改原库=False,业务实验=False,
        清理='继承ImageLease登记及归档后退出核验；临时镜像不保留'),ensure_ascii=False,indent=2))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):
        return
    try:
        execute(args, password, output)
    finally:
        password = None


if __name__ == '__main__':
    main()
