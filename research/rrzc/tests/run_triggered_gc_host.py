#!/usr/bin/env python3
"""先真实设备边界验证，再同负载短配对；单客机，临时镜像归档后回收。"""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from run_rr_path_host import RrRun, preflight, IMAGE_DIR, ROOT
from run_all_blocks_followup_host import PARENT, identity, OLD
from run_joint_host import digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from run_ycsb_quick_host import disk_space, profile8
from run_dbbench_parallel_host import stamp
from analyze_all_blocks_dbbench import selections
from triggered_gc_patch import sources
from ycsb_af import digest, require

LABEL = 'joint-triggered-gc-dbbench-v1-20261026'
FILES = ('run_triggered_gc_host.py', 'build_triggered_gc_guest.py', 'triggered_gc_device.cc',
         'v1_execution_smoke_test.cc', 'build_all_blocks_guest.py', 'build_all_blocks_dbbench_guest.py',
         'run_all_blocks_dbbench_guest.sh', 'strict_stats_guest.py', 'dbbench_space_probe.cc')


def boundary_wrapper(base):
    def once(text, old, new):
        require(text.count(old) == 1, '边界包装器来源锚点不唯一：' + old)
        return text.replace(old, new, 1)
    prefix = base.split('hotspot=0;phase load\n', 1)
    require(len(prefix) == 2, '缺少原业务阶段边界')
    text = once(prefix[0], 'result=$target/$mode', 'result=$target/boundary')
    text = once(text, '"$target/db_bench" "${common[@]}" "$@"',
                '"$target/all_blocks_device" "$result"')
    return text + '''hotspot=0
run_bench > "$result/run.log" 2>&1
snapshot business
printf 'done\\n' > "$result/phase.txt"
'''


def boundary_check(log, native):
    require('已触发GC边界验证通过' in native, '边界程序未通过内容验证')
    require('RR 开始：' not in log, '纯GC边界验证意外发生RR')
    rounds, _ = selections(log.splitlines(), '边界', 'blocks')
    eligible = [r for r in rounds if r.get('触发后约束', {}).get('零缺口仍选区') == '1']
    require(eligible and all(r['空间需求']['缺口字节'] == '0' and r['实际Zone'] is not None and
            r['ZC复制提交Reset闭环'] for r in eligible), '零缺口GC缺少选区及复制映射Reset闭环')
    return dict(通过=True, 零缺口完成清理轮数=len(eligible), 逐轮证据=eligible,
                边界='正常文件写入准备20%取整边界，不修改GC触发或直接调用维护；非性能负载')


def wait_stage(run, tag, wrapper, timeout, mode=None):
    disk_space()
    check_reused_image_budget(run)
    selected = mode if mode is not None else ('blocks' if tag == 'boundary' else tag)
    require(re.fullmatch(r'[a-z0-9-]+', tag) is not None and selected in ('greedy', 'blocks'),
            '实验目录标签或实际策略非法')
    run.root(f'RRZC_ARM_TAG={tag} nohup setsid bash {run.target}/{wrapper} {selected} '
             f'< /dev/null > {run.target}/{tag}.启动.log 2>&1 & echo $! > {run.target}/{tag}.pid')
    started = changed = time.monotonic()
    previous = None
    while time.monotonic() - started < timeout:
        disk_space()
        check_reused_image_budget(run)
        point = run.ssh(f'if test -f {run.target}/{tag}/finished; then cat {run.target}/{tag}/wrapper.exit; '
            f'else echo 运行中; fi; test ! -f {run.target}/{tag}/phase.txt || cat {run.target}/{tag}/phase.txt; '
            f'test ! -f {run.target}/{tag}/run.log || tail -1 {run.target}/{tag}/run.log; '
            f'test ! -f {run.target}/{tag}/load.log || stat -c %s {run.target}/{tag}/load.log; '
            f'test ! -f {run.target}/{tag}/verify.log || stat -c %s {run.target}/{tag}/verify.log; true')
        run.record(当前策略=tag, 最近观测=point)
        print(tag + '：' + point.strip().replace('\n', '；'), flush=True)
        if point != previous:
            previous, changed = point, time.monotonic()
        require(time.monotonic() - changed < 180, '180秒无可见进展，采证并正常关机')
        if point.splitlines()[0] != '运行中':
            require(point.splitlines()[0] == '0', tag + '失败，归档原始证据')
            return
        time.sleep(10)
    raise RuntimeError(tag + '达到预定时间上限，归档并关机')


def check_reused_image_budget(run):
    limit=getattr(run,'max_image_bytes',None)
    image=getattr(run,'args_reuse_image',None)
    if limit is not None and image is not None:
        require(Path(image).stat().st_blocks*512<=limit,
                '复用镜像超过空间上限，采证并正常关机')


def stop_business(run):
    # 异常归档前让确定属于本轮的业务程序正常接收中断，避免边写边打包。
    script = r'''from pathlib import Path
import os,signal,time
root=Path(__import__('sys').argv[1])
import json,shutil
captured=[]
for phase in root.glob('*/phase.txt'):
    name=phase.read_text().strip();before=phase.parent/(name+'.logs.before.txt')
    if not before.is_file():continue
    old=set(before.read_text().splitlines())
    for log in Path('/tmp').glob('zenfs_nvme0n1_*.log'):
        target=phase.parent/('failed.'+name+'.'+log.name)
        if str(log) not in old and not target.exists():
            os.link(log,target);captured.append(str(target))
source=root/'失败源码';source.mkdir(exist_ok=True)
for pattern in ('*.cc','*.h'):
    for p in Path('/home/femu/rocksdb/plugin/zenfs/fs').glob(pattern):
        target=source/p.name
        if not target.exists():shutil.copyfile(p,target)
(root/'失败采证.json').write_text(json.dumps({'新增失败日志':captured,'实际源码目录':str(source)},ensure_ascii=False))
for marker in root.glob('*/bench.pid'):
    pid=int(marker.read_text());proc=Path('/proc')/str(pid)
    if not proc.exists():continue
    executable=proc/'exe'
    try:actual=executable.resolve(strict=True)
    except FileNotFoundError:continue
    if actual not in (root/'db_bench',root/'all_blocks_device',root/'space_goal',
                      root/'colocation_reference',root/'colocation_prototype',root/'colocation_cases',
                      root/'colocation_choice',root/'colocation_credit'):
        raise RuntimeError('业务PID身份不符，拒绝发信号')
    os.kill(pid,signal.SIGINT)
    limit=time.monotonic()+20
    while proc.exists() and time.monotonic()<limit:time.sleep(.1)
    if proc.exists():raise RuntimeError('已中断业务仍未退出，保留证据并正常关机')
'''
    import shlex
    run.root('python3 -c ' + shlex.quote(script) + ' ' + shlex.quote(run.target), 45)


def preserve_unverified_image(run, cleanup):
    """占用核验权限不足时保留镜像并标明状态，不阻断已归档数据的独立分析。"""
    if str(cleanup) != '无法核对本账号进程占用，保留镜像':
        return False
    stage = run.output / '阶段归档.json'
    archive = run.output / (Path(run.target).name + '-' + run.tag + '.tar.gz')
    if not stage.is_file() or run.image_lease is None or not archive.is_file():
        return False
    status = json.loads(stage.read_text())
    if not status['完整'] or status['客机仍存活'] or run.state.get('当前客机PID') is not None:
        return False
    run.image_lease.data.update(status='retained-unverified', reason=str(cleanup),
        archive=str(archive), archive_sha256=digest(archive))
    run.image_lease.save()
    run.record(镜像处理=run.image_lease.data, 收尾待办=str(cleanup),
               阶段='业务已归档且客机退出；保留无法完成占用核验的临时镜像，继续只读分析')
    return True


def execute(args, password, output):
    # RrRun继承DbRun.start/finish_image，接入ImageLease临时镜像管理。
    run = RrRun(args, password, output, 'triggered', 8101, PARENT, retain_image=False)
    run.profile = profile8()
    run.state.update(边界='8GiB单轮定制db_bench；先边界最小验证，后串行配对', GC启用=True)
    error = None
    try:
        run.start()
        run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
        run.deployed = True
        original = identity()
        query = 'cd /home/femu/rocksdb && sha256sum ' + ' '.join(original)
        require(digest_manifest(run.ssh(query, 180)) == original, '原软件或全块对象身份不符')
        upload = [p for p in (output / '源码').iterdir() if p.is_file()]
        run.command(run.scp() + list(map(str, upload)) + [f'femu@127.0.0.1:{run.target}/'])
        run.ssh('cd /home/femu/rocksdb/plugin/zenfs && ' +
            f'git apply --check {run.target}/triggered-gc.patch && git apply {run.target}/triggered-gc.patch && '
            f'git apply --reverse --check {run.target}/triggered-gc.patch && cp {run.target}/rrzc_triggered_gc.h fs/')
        run.record(阶段='重编修正ZenFS翻译单元并链接边界及db_bench客户端；原静态库不变')
        run.ssh(f'python3 {run.target}/build_triggered_gc_guest.py {run.target}', 600)
        run.command(run.scp() + [f'femu@127.0.0.1:{run.target}/客户端身份.json', str(output / '客户端身份.json')])
        run.ssh(f'{run.target}/db_bench --db={run.target}/posix --benchmarks=fillseq,readseq,verifychecksum '
                '--num=1024 --value_size=4096 --disable_wal=1 --compression_type=none '
                f'> {run.target}/普通目录小测.log 2>&1', 120)
        run.record(阶段='编译及普通目录校验通过，开始零缺口真实设备边界验证')
        wait_stage(run, 'boundary', 'run_boundary_guest.sh', 180)
        for name in ('zenfs.run.log', 'run.log'):
            run.command(run.scp() + [f'femu@127.0.0.1:{run.target}/boundary/{name}',
                                     str(output / ('边界-' + name))])
        result = boundary_check((output / '边界-zenfs.run.log').read_text(),
                                (output / '边界-run.log').read_text())
        stamp(output / '边界核验.json', result)
        run.record(阶段='零缺口选区及完整GC闭环通过，开始与上一轮同负载的短配对')
        for mode in ('greedy', 'blocks'):
            run.record(阶段=mode+'：重新初始化模拟盘、装载并运行24批次', 当前策略=mode)
            wait_stage(run, mode, 'run_all_blocks_dbbench_guest.sh', 1800)
        expected = json.loads((output / '源码/expected.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected), 180)) == expected,
                '运行后软件身份改变')
        run.record(阶段='两组完成，归档后正常关机并回收临时镜像')
    except BaseException as failure:
        error = failure
        run.record(原始错误=repr(failure))
        if run.deployed:
            try:
                stop_business(run)
            except Exception as stopped:
                run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        try:
            run.finish(error)
        except Exception as cleanup:
            retained = preserve_unverified_image(run, cleanup)
            if error is None and not retained:
                raise
            if error is not None:
                run.record(收尾另有错误=repr(cleanup))
    subprocess.run([sys.executable, str(output / '源码/analyze_all_blocks_dbbench.py'),
                    str(output / args.label), str(output / '分析')], check=True)
    deleted = run.image_lease.data['status'] == 'deleted'
    run.record(阶段='边界验证和短配对分析完成，客机退出；临时镜像'+('已回收' if deleted else '保留待核验'),
               已完成=['boundary', 'greedy', 'blocks'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--label', default=LABEL)
    args = parser.parse_args()
    require(re.fullmatch(r'joint-[a-z0-9-]+', args.label) is not None, '非法实验标签')
    budget = preflight()
    require(PARENT.is_file() and not running_parent(PARENT), '父镜像不可用')
    require(digest(ROOT.parents[1] / 'build-femu/qemu-system-x86_64') ==
            json.loads((OLD / '预注册计划.json').read_text())['FEMU_SHA256'], 'FEMU版本改变')
    output = IMAGE_DIR / (args.label + '-results')
    require(not output.exists(), '不覆盖已有实验')
    if args.check:
        print(json.dumps(budget, ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('边界及短配对认证（仅内存）：')
    output.mkdir()
    frozen = output / '源码'
    frozen.mkdir()
    for name in FILES:
        shutil.copyfile(ROOT / 'tests' / name, frozen / name)
    shutil.copyfile(ROOT / 'tests/triggered_gc_device.cc', frozen / 'all_blocks_device.cc')
    shutil.copyfile(ROOT / 'model/rrzc_triggered_gc.h', frozen / 'rrzc_triggered_gc.h')
    shutil.copyfile(ROOT / 'zenfs-triggered-gc.patch', frozen / 'triggered-gc.patch')
    shutil.copyfile(ROOT / 'analyze_all_blocks_dbbench.py', frozen / 'analyze_all_blocks_dbbench.py')
    (frozen / 'run_boundary_guest.sh').write_text(boundary_wrapper((frozen / 'run_all_blocks_dbbench_guest.sh').read_text()))
    expected = identity()
    fs, inc = sources(*[(OLD / '源码' / ('after-' + name)).read_text()
                       for name in ('fs_zenfs.cc', 'rrzc_direct_zenfs.inc')])
    for name, text in (('fs_zenfs.cc', fs), ('rrzc_direct_zenfs.inc', inc)):
        expected['plugin/zenfs/fs/' + name] = hashlib.sha256(text.encode()).hexdigest()
    expected['plugin/zenfs/fs/rrzc_triggered_gc.h'] = digest(frozen / 'rrzc_triggered_gc.h')
    (frozen / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2))
    stamp(output / '预注册计划.json', dict(资源=budget, 父镜像=str(PARENT),
        目的='隔离已触发GC后零缺口跳过的修正，收益公式不变；先最小设备验证再原负载短配对',
        平台='8GiB/64Zone/128MiB、RR1024、GC20%、未锁页、单台串行',
        负载='沿用上一轮1048576键×4KiB，786432读/589824写，24批每4批换热点，无WAL、不限速',
        对照='共同候选和安全检查下最少有效量Greedy；修正后全块模型。分别重新装载，不声称相同物理布局',
        模型变化='仅GC已触发后零空间缺口仍评估一次清理；读取预测、留存、RR成本公式不变',
        入口='RrRun继承DbRun的ImageLease生命周期；明确临时镜像，不作下一轮父镜像',
        收尾='成功失败归档哈希核验并正常关机；退出、占用、子依赖核验后回收临时镜像',
        停止条件='边界失败不启动配对；每臂30分钟、180秒无可见进展提前采证关机'))
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        execute(args, password, output)
    finally:
        password = None


if __name__ == '__main__':
    main()
