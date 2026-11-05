#!/usr/bin/env python3
"""独立db_bench实验；按全局资源预算并行，逐客机准确采证和正常关机。"""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

from run_joint_host import JointRun, PROJECT, IMAGE_DIR, digest_manifest
from run_tail_ablation_fixed_pair import FixedPair, running_parent
from run_rr_fair_long_v2_host import CollectedRun
from run_ycsb_af_host import SuiteRun, resource_sample
from run_ycsb_quick_host import profile8, disk_space
from run_ycsb_headroom_host import PARENT, SOURCE
from ycsb_af import digest, require
from image_lifecycle import ImageLease

ROOT = PROJECT/'research/rrzc'
LABEL = 'joint-dbbench8-parallel-v2-20261023'
RECORDS = 1048576
OLD_TOOLS = '/home/femu/joint-ycsb32-af-v3-20261023'


def stamp(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def capacity():
    memory = dict(s.split(':', 1) for s in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0])*1024
    free = os.statvfs(IMAGE_DIR)
    free = free.f_bavail*free.f_frsize
    cpus = sorted(os.sched_getaffinity(0))
    maximum = min((available-(16<<30))//(12<<30), (free-(24<<30))//(6<<30), (len(cpus)-4)//4)
    require(maximum >= 1, '不足一台客机及原24GiB磁盘、16GiB内存余量，停止')
    disk_space()
    return dict(可用内存字节=available, data可用字节=free, 逻辑CPU=cpus,
        资源允许并发=int(maximum), 每台预算内存字节=12<<30, 每台磁盘预算字节=6<<30,
        内存保留字节=16<<30, data保留字节=24<<30)


class DbRun(JointRun):
    command = SuiteRun.command
    close_guest = CollectedRun.close_guest

    def __init__(self, args, password, output, tag, port, parent=PARENT, *, retain_image=None):
        self.port = str(port)
        super().__init__(args, password, output, profile8())
        self.parent_image = parent
        self.target = '/home/femu/'+args.label
        self.tag = tag
        self.deployed = False
        self.retain_image = tag in ('prepare', 'build') if retain_image is None else retain_image
        self.image_lease = None
        self.qemu_digest = digest(PROJECT/'build-femu/qemu-system-x86_64')
        self.state.update(阶段=tag+'准备', 所属实验=tag, SSH端口=port,
            边界='db_bench，RR1024、GC20%、未锁页；允许并行，性能含共享宿主干扰')

    def start(self, mode=None):
        disk_space()
        memory = dict(s.split(':', 1) for s in Path('/proc/meminfo').read_text().splitlines())
        require(int(memory['MemAvailable'].split()[0])*1024 >= 28<<30, '启动时内存不足新客机及16GiB余量')
        require(digest(PROJECT/'build-femu/qemu-system-x86_64') == self.qemu_digest, 'FEMU身份漂移')
        self.image_lease = ImageLease(IMAGE_DIR, IMAGE_DIR/(self.label_prefix+'-'+self.tag+'.qcow2'),
            self.parent_image, self.output, PROJECT/'build-femu/qemu-img', checkpoint=self.retain_image)
        self.image_lease.reserve()
        try:
            FixedPair.start(self, self.tag)
        finally:
            self.image_lease.cancel_uncreated()
            pidfile = Path('/tmp')/(self.label_prefix+'-'+self.tag+'.pid')
            image = IMAGE_DIR/(self.label_prefix+'-'+self.tag+'.qcow2')
            if pidfile.is_file():
                pid = int(pidfile.read_text())
                proc = Path('/proc')/str(pid)
                if proc.exists() and str(image).encode() in (proc/'cmdline').read_bytes():
                    self.record(当前客机PID=pid, 当前镜像=str(image), QMP=str(self.qmp))
        self.record(当前阶段=self.tag, 资源观测=resource_sample(self.state['当前客机PID']))

    def image_created(self, image, parent):
        self.image_lease.created(image, parent)

    def finish_image(self, archive, expected, *, archived, guest_exited):
        if self.image_lease is not None:
            self.image_lease.finish(archive, expected, archived=archived, guest_exited=guest_exited)
            self.record(镜像处理=self.image_lease.data)

    def finish(self, error=None):
        errors = []
        archive, expected = None, None
        try:
            if self.deployed:
                # 异常时先冻结诊断副本；业务目录不是完整NAND快照。
                if error is not None and self.tag != 'prepare':
                    try:
                        self.root(f'python3 {self.target}/capture_dbbench_guest.py capture {self.target}/{self.tag}', 45)
                    except Exception as failure:
                        errors.append('异常采证：'+str(failure))
                    try:
                        self.root(f'python3 {self.target}/capture_dbbench_guest.py interrupt {self.target}/{self.tag}', 30)
                    except Exception as failure:
                        errors.append('正常中断业务：'+str(failure))
                remote = self.target+'-'+self.tag+'.tar.gz'
                self.ssh(f'test ! -e {remote} && tar --exclude="{Path(self.target).name}/posix" '
                         f'-czf {remote} -C /home/femu {Path(self.target).name}', 180)
                expected = self.ssh('sha256sum '+remote).split()[0]
                self.command(self.scp()+[f'femu@127.0.0.1:{remote}', str(self.output)], 180)
                require(digest(self.output/Path(remote).name) == expected, '采证包哈希不同')
                self.command(['tar', '-xzf', str(self.output/Path(remote).name), '-C', str(self.output)], 120)
                archive = self.output/Path(remote).name
        except Exception as failure:
            errors.append('归档：'+str(failure))
        try:
            self.close_guest()
        except Exception as failure:
            errors.append('正常关机：'+str(failure))
        pid = self.state.get('当前客机PID')
        alive = bool(pid and Path('/proc', str(pid)).exists())
        if not alive:
            self.record(当前客机PID=None, 当前镜像=None, 阶段='已归档并退出' if not errors else '归档异常但客机已退出')
            if self.socket:
                reply = subprocess.run(['ssh', '-S', self.socket, '-O', 'exit', '-p', self.port,
                    'femu@127.0.0.1'], capture_output=True, text=True, timeout=15)
                (self.output/'SSH收尾.log').write_text(reply.stdout+reply.stderr)
        stamp(self.output/'阶段归档.json', dict(完整=not errors, 错误=errors, 客机仍存活=alive,
            业务成功=error is None, 原始错误=None if error is None else repr(error)))
        self.finish_image(archive, expected, archived=not errors, guest_exited=not alive)
        require(not errors and not alive, '归档或关机未通过：'+str(errors))


def prepare(args, password, output):
    out = output/'prepare'
    out.mkdir()
    run = DbRun(args, password, out, 'prepare', 8101)
    error = None
    try:
        run.start()
        run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
        run.deployed = True
        expected = json.loads((SOURCE/'同库身份.json').read_text())
        expected = {k:v for k,v in expected.items() if k.startswith('plugin/zenfs/fs/') or
                    k == 'librocksdb_debug.a' or k == OLD_TOOLS+'/quick_device_probe'}
        query = 'cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        require(digest_manifest(run.ssh(query, 180)) == expected, '复用库和模型身份不符')
        run.record(阶段='编译/核验当前库的db_bench；不改模型及阈值')
        run.ssh(f'cd /home/femu/rocksdb && make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 '
                f'ROCKSDB_PLUGINS=zenfs -j2 db_bench > {run.target}/编译.log 2>&1', 600)
        require(digest_manifest(run.ssh(query, 180)) == expected, '构建改变已验收库或模型')
        expected['db_bench'] = run.ssh('cd /home/femu/rocksdb; sha256sum db_bench').split()[0]
        stamp(output/'同库身份.json', expected)
        run.ssh(f'cd /home/femu/rocksdb; ./db_bench --help > {run.target}/帮助.txt 2>&1; '
                f'cp tools/db_bench_tool.cc monitoring/histogram.cc {run.target}/')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/db_bench_tool.cc',
                              f'femu@127.0.0.1:{run.target}/histogram.cc', str(output/'源码')])
        run.ssh(f'cd /home/femu/rocksdb && ./db_bench --db={run.target}/posix '
            '--benchmarks=fillseq,readrandomwriterandom --num=1024 --reads=8192 --writes=1024 '
            '--value_size=4096 --compression_type=none --disable_wal=1 --histogram=1 '
            f'--stats_interval=1000 --stats_interval_seconds=1 --seed=20261023 > {run.target}/本地小测.log 2>&1', 120)
        run.record(阶段='db_bench编译及普通目录小测通过，准备真实ZNS小测')
    except BaseException as failure:
        error = failure
        raise
    finally:
        try:
            run.finish(error)
        except Exception as cleanup:
            if error is None:
                raise
            run.record(收尾另有错误=str(cleanup))
    stamp(output/'准备完成.json', dict(父镜像=str(IMAGE_DIR/(args.label+'-prepare.qcow2')),
        软件身份SHA256=digest(output/'同库身份.json'), 说明='只持久化软件与系统盘；未继承内存NAND或RC'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--phase', choices=('prepare', 'run', 'resume'), default='prepare')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--allow-unlocked', action='store_true')
    args = parser.parse_args()
    require(args.label == LABEL and args.allow_unlocked, '须使用独立标签并明确未锁页')
    output = IMAGE_DIR/(args.label+'-results')
    require(PARENT.is_file() and not running_parent(PARENT), '父镜像不可用')
    reply = subprocess.run(['ps', '-C', 'qemu-system-x86_64', '-o', 'args='], capture_output=True, text=True)
    require(str(IMAGE_DIR)+'/' not in reply.stdout, '本项目仍有旧客机，不叠加')
    budget = capacity()
    for port in (8101, 8102):
        with socket.socket() as sock:
            require(sock.connect_ex(('127.0.0.1', port)) != 0, '新端口被占用')
    if args.check:
        print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('db_bench并行实验认证（仅内存）：')
    if args.phase == 'prepare':
        output.mkdir()
        (output/'源码').mkdir()
        shutil.copyfile(__file__, output/'源码/run_dbbench_parallel_prepare_host.py')
        stamp(output/'预注册计划.json', dict(目的='用户要求暂停YCSB，db_bench阈值1024并最大化安全并行',
            资源=budget, 平台=profile8(), 最大并行=int(budget['资源允许并发']),
            FEMU二进制SHA256=digest(PROJECT/'build-femu/qemu-system-x86_64'),
            隔离='独立镜像、端口和CPU集合；保留共享宿主对吞吐及长尾的影响',
            保护='24GiB data保留、16GiB内存保留不降低；每台12GiB内存及6GiB磁盘预算'))
    else:
        require((output/'准备完成.json').is_file(), '尚未完成db_bench准备')
    if args.detach:
        reader, writer = os.pipe()
        pid = os.fork()
        if pid:
            os.close(writer); require(os.read(reader, 1) == b'1', '监督分离失败'); os.close(reader)
            print(f'db_bench监督PID={pid}，目录={output}', flush=True); return
        os.close(reader); os.setsid()
        fd = os.open(output/(args.phase+'-监督.log'), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        os.dup2(fd, 1); os.dup2(fd, 2); os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(writer, b'1'); os.close(writer)
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        if args.phase == 'prepare':
            prepare(args, password, output)
        else:
            from dbbench_suite import execute
            execute(args, password, output, budget)
    finally:
        password = None


if __name__ == '__main__':
    main()
