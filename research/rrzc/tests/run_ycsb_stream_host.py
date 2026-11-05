#!/usr/bin/env python3
"""修复采样OOM后从已验收库直接串行重跑F/B/E；旧失败结果不覆盖、不筛选重跑。"""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from run_ycsb_af_host import SuiteRun, ROOT, PROJECT, IMAGE_DIR, host_resources
from ycsb_af import digest, require, WORKLOADS, LABELS, RECORDS, OPERATIONS
from analyze_ycsb_af import compare_pair

SOURCE = IMAGE_DIR/'joint-ycsb32-af-v3-20261023-results'
BUILD = IMAGE_DIR/'joint-ycsb32-af-v3-20261023-build.qcow2'
LABEL = 'joint-ycsb32-stream-v3-20261023'
ORDER = ('f', 'b', 'e')
TESTS = ROOT/'tests'


class ReadOnlyFailure(RuntimeError):
    pass


def bounded_query(command, run_once, record, *, wait=time.sleep, limit=3):
    """只读调用重试不启动业务、不清零无进展计时；非255业务退出立即失败。"""
    for attempt in range(limit):
        try:
            result = run_once(command)
        except subprocess.TimeoutExpired as error:
            record(尝试=attempt+1, 类型='只读查询超时', 原始反馈=str(error))
            retry = True
        else:
            if result.returncode == 0:
                return result.stdout
            record(尝试=attempt+1, 类型='只读查询失败', 退出码=result.returncode, 原始反馈=result.stdout)
            retry = result.returncode == 255
            if not retry:
                raise ReadOnlyFailure('只读查询返回非通信错误，退出'+str(result.returncode))
        if not retry or attempt+1 == limit:
            raise ReadOnlyFailure('只读查询连续失败，已保留全部原始反馈；不重启业务')
        wait(2*(attempt+1))
    raise AssertionError('不可达')


class StreamRun(SuiteRun):
    def __init__(self, *args):
        super().__init__(*args)
        self.target = '/home/femu/'+SOURCE.name.removesuffix('-results')
        self.state.update(阶段='流式采样修复，准备F/B/E小测后重跑',
                          模式顺序=[f'{s}-{w}-{m}' for s in ('smoke', 'full') for w in ORDER for m in modes(w)],
                          原失败目录=str(SOURCE), 采样方式='64KiB增量读取，1MiB分块哈希',
                          单日志预算字节=4 << 30, 不重启业务=True)

    def root(self, command, timeout=45):
        old = f'nohup setsid bash {self.target}/run_ycsb_af_guest.sh '
        if command.startswith(old):
            command = command.replace(old, f'nohup setsid bash {self.target}/run_ycsb_af_stream_guest.sh ', 1)
        return super().root(command, timeout)

    def ssh(self, command, timeout=30):
        expected = f'python3 {getattr(self,"control","")}/capture_ycsb_guest.py progress '
        if not command.startswith(expected):
            return super().ssh(command, timeout)
        def run_once(cmd):
            argv = ['ssh', '-S', self.socket, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
                    '-p', self.port, 'femu@127.0.0.1', cmd]
            start = time.monotonic()
            result = subprocess.run(argv, cwd=PROJECT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=40)
            with (self.output/'只读通信.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(UTC=datetime.now(timezone.utc).isoformat(),
                    参数=argv, 退出码=result.returncode, 耗时秒=time.monotonic()-start,
                    原始输出=result.stdout), ensure_ascii=False)+'\n')
            return result
        def record(**event):
            with (self.output/'只读重试.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(UTC=datetime.now(timezone.utc).isoformat(), **event), ensure_ascii=False)+'\n')
        return bounded_query(command, run_once, record)

    def start(self, mode):
        super().start(mode)
        payload = self.output/'源码/run_ycsb_af_stream_guest.sh'
        manifest = self.output/'源码/stream.software.sha256'
        self.command(self.scp()+[str(payload), str(manifest), f'femu@127.0.0.1:{self.target}/'])
        require(self.ssh(f'sha256sum {self.target}/run_ycsb_af_stream_guest.sh').split()[0] == digest(payload),
                '新包装器部署身份不符')
        free = int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
        require(free >= 6 << 30, '根盘不足6GiB，停止不降门槛')
        self.record(阶段=mode+'流式采证及新包装器部署通过', 客机根盘可用字节=free)

    def collect(self, out, remote):
        super().collect(out, remote)
        # 已结束业务的完整副本若字节一致，宿主也复用同一inode；不删路径或原始数据。
        if remote == self.control:
            tag = self.state['当前阶段']
            original = out/tag/'zenfs.run.log'
            duplicate = out/Path(self.control).name/(tag+'-final')/'zenfs.partial.log'
            if original.exists() and duplicate.exists() and digest(original) == digest(duplicate):
                temporary = duplicate.with_suffix('.dedup.tmp')
                os.link(original, temporary)
                temporary.replace(duplicate)

    def prepare_root(self):
        """仅新子镜像内，使用已存在的VG空闲扩容根LV；不动分区或原父镜像。"""
        self.parent_image = BUILD
        out = self.output/'prepare'
        out.mkdir()
        original = json.loads((SOURCE/'同库身份.json').read_text())
        try:
            # 准备阶段目的就是增加隔离根盘空间，不能调用业务阶段的6GiB门禁。
            # 宿主内存、data余量、单台串行和原镜像保护仍由SuiteRun.start核验。
            SuiteRun.start(self, 'prepare')
            from run_joint_host import digest_manifest
            actual = digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(original), 180))
            require(actual == original, '准备前库或旧客户端身份不符')
            inspect = self.root('lsblk -b -o NAME,SIZE,FSTYPE,MOUNTPOINTS; '
                'findmnt -n -o SOURCE,FSTYPE /; vgs --units b --nosuffix -o vg_name,vg_free; '
                'lvs --units b --nosuffix -o lv_path,lv_size', 30)
            (out/'根盘准备前.log').write_text(inspect)
            print(inspect, flush=True)
            # 命令内重新校验唯一目标和VG余量，若布局不符则拒绝，不猜测分区。
            command = '''set -e
test "$(findmnt -n -o SOURCE /)" = /dev/mapper/ubuntu--vg-ubuntu--lv
test "$(findmnt -n -o FSTYPE /)" = ext4
test -b /dev/ubuntu-vg/ubuntu-lv
vgs --units b --nosuffix --noheadings -o vg_free ubuntu-vg | python3 -c 'import sys;assert float(sys.stdin.read().strip()) >= 17179869184,"VG现有空闲不足16GiB，不改分区或父镜像"'
lvextend --resizefs --size +16G /dev/ubuntu-vg/ubuntu-lv
df -h /
'''
            expanded = self.root(command, 120)
            (out/'根盘扩容.log').write_text(expanded)
            print(expanded, flush=True)
            free = int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            require(free >= 20 << 30, '隔离根盘扩容后余量不足20GiB')
            actual = digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(original), 180))
            require(actual == original, '准备后核心库或旧程序发生变化')
            self.record(阶段='新子镜像根盘扩容及原软件身份核验通过', 客机根盘可用字节=free)
        finally:
            self.finish(out)
        self.state['已完成'].append('prepare')
        self.record(阶段='根盘准备已归档，客机正常关闭；SSD仍为32GiB')


def modes(letter):
    out = list(LABELS)
    return out if list(WORKLOADS).index(letter) % 2 == 0 else list(reversed(out))


def freeze(output):
    shutil.copytree(SOURCE/'源码', output/'源码')
    frozen = output/'源码'
    # 新实验自己的快照，原冻结证据逐字节保留；只替换采证主体，适配器仍为旧版本。
    source = TESTS/'capture_ycsb_stream_guest.py'
    (frozen/'capture_ycsb_guest.py').write_bytes(source.read_bytes())
    for file in (Path(__file__), TESTS/'run_ycsb_af_stream_guest.sh', TESTS/'test_ycsb_stream.py'):
        (frozen/file.name).write_bytes(file.read_bytes())
    original = json.loads((SOURCE/'同库身份.json').read_text())
    new = dict(original)
    target = '/home/femu/'+SOURCE.name.removesuffix('-results')
    new[target+'/run_ycsb_af_stream_guest.sh'] = digest(TESTS/'run_ycsb_af_stream_guest.sh')
    (output/'同库身份.json').write_text(json.dumps(new, ensure_ascii=False, indent=2))
    (frozen/'stream.software.sha256').write_text(''.join(h+'  '+n+'\n' for n, h in new.items()))
    (output/'平台配置.json').write_bytes((SOURCE/'平台配置.json').read_bytes())
    (output/'继承身份.json').write_text(json.dumps(dict(原软件清单=digest(SOURCE/'同库身份.json'),
        采证主体=digest(source), 包装器=digest(TESTS/'run_ycsb_af_stream_guest.sh'),
        客户端与模型不变=True), ensure_ascii=False, indent=2))


def preflight(args):
    require(args.allow_unlocked, '仍须显式批准本轮未锁页')
    require(re.fullmatch(r'joint-[a-z0-9-]+', args.label), '标签非法')
    output = IMAGE_DIR/(args.label+'-results')
    require(not output.exists() and BUILD.is_file(), '新结果目录已存在或源构建缺失')
    source_plan = json.loads((SOURCE/'预注册计划.json').read_text())
    binary = digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary == source_plan['FEMU二进制SHA256'], '严格FEMU版本漂移')
    for name in ('run_ycsb_af_host.py', 'analyze_ycsb_af.py', 'ycsb_af.py', 'plot_ycsb_af.py'):
        path = TESTS/name if name == 'run_ycsb_af_host.py' else ROOT/name
        require(digest(path) == digest(SOURCE/'源码'/name), '未声明改变旧入口或模型分析：'+name)
    state = json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None, '失败客机尚未关闭')
    for tag in ('build', *[f'smoke-{w}-{m}' for w in WORKLOADS for m in LABELS]):
        a = json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(a['完整'] and not a['客机仍存活'], '源构建/小测未完成归档')
    for tag in ('prepare', 'build', *[f'{s}-{w}-{m}' for s in ('smoke', 'full') for w in ORDER for m in LABELS]):
        label = args.label+'-'+tag
        for path in (IMAGE_DIR/(label+'.qcow2'), Path('/tmp')/(label+'.pid'), Path('/tmp')/(label+'.qmp'), Path('/tmp')/(label+'-ssh.sock')):
            require(not path.exists(), '新目标已存在：'+str(path))
    profile = json.loads((SOURCE/'平台配置.json').read_text())
    return output, profile, binary, host_resources(profile)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-unlocked', action='store_true')
    p.add_argument('--label', default=LABEL)
    p.add_argument('--check', action='store_true')
    p.add_argument('--detach', action='store_true')
    a = p.parse_args()
    output, profile, binary, resources = preflight(a)
    if a.check:
        print(json.dumps(dict(预检通过=True, 资源=resources, 重跑顺序=list(ORDER)), ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('修复后F/B/E串行重跑认证（仅驻留内存）：')
    output.mkdir()
    freeze(output)
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='修复已确认的采样OOM后重跑，不调整模型以追求收益', 原失败=str(SOURCE),
        FEMU二进制SHA256=binary, 初始资源=resources, 顺序=list(ORDER),
        不变='32GiB/256Zone、4GiB客机、严格RR1024、GC20%、6291456键/120万业务、同种子、不限速、无WAL缓存、原模型及全部安全规则',
        修复='64KiB增量进度、1MiB分块哈希；只读查询最多3次通信重试，业务不重启、数据错误不重试',
        系统盘='仅新prepare子镜像使用已有VG空闲扩大根LV16GiB，核验前后软件身份不变；原父镜像及分区不动',
        日志预算='单文件由旧实验2GiB显式改为4GiB，结束日志硬链接避免重复占根盘；根盘6GiB及宿主/data保护保留',
        验收='先本轮F/B/E两策略真ZNS小测，通过后正式配对；请求/最终值/配置/源码/实际评分均验收；无收益也保留',
        收尾='串行一台，成功失败均采证、归档核验、正常关机；不把qcow2当NAND快照',
        口径='未锁页，单轮；维护含装载、Run和Cleanup，不含校验；API非裸IO；块擦除由Reset推导'), ensure_ascii=False, indent=2))
    if a.detach:
        r, w = os.pipe()
        pid = os.fork()
        if pid:
            os.close(w)
            require(os.read(r, 1) == b'1', '重跑监督分离失败')
            os.close(r)
            print(f'重跑监督PID={pid}，状态={output / "状态.json"}', flush=True)
            return
        os.close(r)
        os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(fd, 1); os.dup2(fd, 2); os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(w, b'1'); os.close(w)
    run = StreamRun(a, password, output, profile)
    run.qemu_digest = binary
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        run.record(阶段='采样OOM修复已冻结，准备新小测后重跑')
        run.prepare_root()
        prepared = IMAGE_DIR/(a.label+'-prepare.qcow2')
        run.command(['./build-femu/qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
                     str(prepared), str(IMAGE_DIR/(a.label+'-build.qcow2'))])
        for stage in ('smoke', 'full'):
            for letter in ORDER:
                for mode in modes(letter):
                    run.arm(stage, letter, mode)
                records, ops = (8192, 8192) if stage == 'smoke' else (RECORDS, OPERATIONS)
                result = compare_pair(output, stage, letter, records, ops)
                (output/f'{stage}-{letter}-配对核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                run.record(阶段=stage+'-'+letter+'修复后配对验收通过，客机均已关闭', 最近差值=result['差值'])
                if stage == 'full':
                    from plot_ycsb_af import render
                    render(output)
        run.record(阶段='F/B/E修复后正式配对完成，图表和CSV已生成，全部客机关闭')
    except BaseException as error:
        run.record(阶段='修复后实验停止，原始错误保留，不自动重跑', 错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
