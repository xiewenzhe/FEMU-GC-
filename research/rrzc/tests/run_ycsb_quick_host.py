#!/usr/bin/env python3
"""独立8GiB快速F配对：复用已验收库和客户端，双盘保护、真数据校验、串行收尾。"""
import argparse
import copy
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sys
import time

from run_ycsb_stream_host import StreamRun, SOURCE, ROOT, PROJECT, IMAGE_DIR
from run_ycsb_af_host import SuiteRun, host_resources, resource_sample
from run_tail_ablation_fixed_pair import FixedPair, running_parent
from run_joint_host import digest_manifest
from finerr_profile import load, settings
from ycsb_af import digest, require, LABELS

LABEL = 'joint-ycsb8-f-v2-20261023'
PARENT = IMAGE_DIR/'joint-ycsb32-stream-v3-20261023-prepare.qcow2'
RECORDS, OPERATIONS = 1572864, 300000
TESTS = ROOT/'tests'
DATA_RESERVE, SYSTEM_RESERVE = 24 << 30, 12 << 30
INITIAL_BUDGET, EXIT_BUDGET = 12 << 30, 2 << 30


def replace_once(text, before, after):
    require(text.count(before) == 1, '冻结源结构改变，拒绝猜测替换：'+before)
    return text.replace(before, after, 1)


def profile8():
    p = copy.deepcopy(load())
    p['名称'] = '8GiB快速F单轮配对，不是32GiB全规模或稳态实验'
    p['硬件']['每PlaneBlock数'] = 64
    p['命名空间'].update(容量字节=8 << 30, Zone数量=64)
    return p


def disk_space(*, initial=False, stat=os.statvfs):
    """不降低原data余量；启动另留12GiB预算，运行提前2GiB收尾。"""
    rows = {}
    for name, path, reserve in (('系统盘', Path('/'), SYSTEM_RESERVE),
                                 ('data盘', IMAGE_DIR, DATA_RESERVE)):
        fs = stat(path)
        free = fs.f_bavail*fs.f_frsize
        needed = reserve+(INITIAL_BUDGET if initial else EXIT_BUDGET)
        require(free >= needed, f'{name}可用{free}字节低于{needed}字节，保留收尾空间，拒绝继续')
        rows[name] = dict(可用字节=free, 本阶段所需字节=needed)
    return rows


def wrapper_source():
    text = (TESTS/'run_ycsb_af_stream_guest.sh').read_text()
    text = replace_once(text, '== 34359738368', '== 8589934592')
    text = replace_once(text, 'len(z)==256', 'len(z)==64')
    text = text.replace('"$target/sst_device_probe"', '"$target/quick_device_probe"')
    text = text.replace('stream.software.sha256', 'quick.software.sha256')
    text = text.replace('4294967296', '1073741824')
    text = replace_once(text, '(4<<30,4<<30)', '(1<<30,1<<30)')
    return '# 本文件由独立8GiB入口从已验收包装器精确适配；机制环境与请求语义不变。\n'+text


def probe_source():
    text = (TESTS/'suite_device_probe.cc').read_text()
    text = replace_once(text, 'r.zones.size()!=256', 'r.zones.size()!=64')
    return text.replace('32GiB', '8GiB')


def analysis_source():
    text = (ROOT/'analyze_ycsb_af.py').read_text()
    text = replace_once(text, "len(d['Zone']) == 256", "len(d['Zone']) == 64")
    text = replace_once(text, 'len(initial) == 256', 'len(initial) == 64')
    return text.replace('32GiB', '8GiB')


def plot_source():
    text = (ROOT/'plot_ycsb_af.py').read_text()
    text = replace_once(text, "subtitle = f'32 GiB，未锁页，不限速，单轮配对；已完成 {len(available)}/6 种负载'",
        "subtitle = '8 GiB，YCSB-F，1572864键/30万请求；未锁页，不限速，单轮非稳态'")
    return text.replace('# 32GiB A至F实测结果', '# 8GiB YCSB-F快速配对实测结果')


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze(output, profile):
    shutil.copytree(SOURCE/'源码', output/'源码')
    frozen = output/'源码'
    core = (TESTS/'capture_ycsb_stream_guest.py').read_text()
    core = replace_once(core, "target/'sst_device_probe'", "target/'quick_device_probe'")
    (frozen/'capture_ycsb_guest.py').write_text(core)
    for name, text in (('run_ycsb_quick_guest.sh', wrapper_source()),
                       ('quick_device_probe.cc', probe_source()),
                       ('analyze_ycsb_quick.py', analysis_source()),
                       ('plot_ycsb_quick.py', plot_source())):
        (frozen/name).write_text(text)
    for source in (Path(__file__), TESTS/'test_ycsb_quick.py', TESTS/'run_ycsb_stream_host.py'):
        shutil.copyfile(source, frozen/source.name)
    original = json.loads((SOURCE/'同库身份.json').read_text())
    target = '/home/femu/'+SOURCE.name.removesuffix('-results')
    original[target+'/run_ycsb_quick_guest.sh'] = digest(frozen/'run_ycsb_quick_guest.sh')
    manifest(output, original)
    (output/'平台配置.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    (output/'接线变化.json').write_text(json.dumps(dict(
        原软件清单SHA256=digest(SOURCE/'同库身份.json'),
        原父镜像=str(PARENT), 适配='仅64Zone容量验收、探针、图表标注及1GiB日志上限；未更换库或客户端',
        新接线SHA256={n:digest(frozen/n) for n in ('run_ycsb_quick_guest.sh',
            'quick_device_probe.cc','analyze_ycsb_quick.py','plot_ycsb_quick.py','capture_ycsb_guest.py')}
    ), ensure_ascii=False, indent=2))


def manifest(output, values):
    (output/'同库身份.json').write_text(json.dumps(values, ensure_ascii=False, indent=2))
    (output/'源码/quick.software.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in values.items()))


class QuickRun(StreamRun):
    # 新的独立实验可显式缩小装载；原入口默认规模及已冻结旧试次不变。
    record_count = RECORDS

    def __init__(self, *args):
        super().__init__(*args)
        self.parent_image = PARENT
        self.state.update(边界='8GiB快速F，单轮非稳态；API不是裸设备IO，不预设正收益',
            模式顺序=['build', 'smoke-f-space', 'smoke-f-model', 'full-f-space', 'full-f-model'],
            单日志预算字节=1 << 30, 总装载键数=RECORDS, 正式业务请求数=OPERATIONS)

    def root(self, command, timeout=45):
        old = f'nohup setsid bash {self.target}/run_ycsb_af_guest.sh '
        if command.startswith(old):
            command = command.replace(old, f'nohup setsid bash {self.target}/run_ycsb_quick_guest.sh ', 1)
        return FixedPair.root(self, command, timeout)

    def start(self, mode):
        disk_space(initial=False)
        # 沿用24GiB data保护、单台串行、16GiB内存余量、精确PID及采证部署。
        SuiteRun.start(self, mode)
        self.command(self.scp()+[str(self.output/'源码'/n) for n in
            ('run_ycsb_quick_guest.sh','quick.software.sha256','quick_device_probe.cc')]+
            [f'femu@127.0.0.1:{self.target}/'])
        require(self.ssh(f'sha256sum {self.target}/run_ycsb_quick_guest.sh').split()[0] ==
                digest(self.output/'源码/run_ycsb_quick_guest.sh'), '8GiB包装器部署不符')
        free = int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
        require(free >= 6 << 30, '客机根盘不足6GiB，停止不降门槛')
        self.record(阶段=mode+'：8GiB接线通过', 客机根盘可用字节=free,
                    最近观测=None, 客机结果目录=None, 磁盘保护=disk_space())

    def build(self):
        out = self.output/'build'
        out.mkdir()
        error = None
        try:
            # 父镜像已保留旧build采证目录；使用独立阶段，拒绝覆盖历史证据。
            self.start('quick-prepare')
            original = json.loads((SOURCE/'同库身份.json').read_text())
            query = 'cd /home/femu/rocksdb; sha256sum '+' '.join(original)
            require(digest_manifest(self.ssh(query, 180)) == original, '复用软件身份不符')
            command = (f'cd /home/femu/rocksdb; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                '-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude '
                f'{self.target}/quick_device_probe.cc librocksdb_debug.a '
                '-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt '
                f'-o {self.target}/quick_device_probe > {self.target}/quick-probe-build.log 2>&1')
            self.ssh(command, 180)
            require(digest_manifest(self.ssh(query, 180)) == original, '探针编译改变原软件')
            expected = json.loads((self.output/'同库身份.json').read_text())
            expected[self.target+'/quick_device_probe'] = self.ssh(f'sha256sum {self.target}/quick_device_probe').split()[0]
            manifest(self.output, expected)
            self.command(self.scp()+[str(self.output/'源码/quick.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.root("printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler")
            raw = self.root(f'{self.target}/quick_device_probe {self.target}/quick-probe.log')
            data = json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
            require(len(data['Zone']) == 64 and data['每区块数'] == 128 and data['RR阈值'] == 1024,
                    '8GiB设备探针真实验收失败')
            (out/'设备探针.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except BaseException as cause:
            error = cause
            self.record(原始准备错误=type(cause).__name__+'：'+str(cause))
            raise
        finally:
            try:
                self.finish(out)
            except Exception as cleanup:
                if error is None:
                    raise
                self.record(准备收尾另有错误=str(cleanup))
        self.command(['./build-femu/qemu-img','create','-f','qcow2','-F','qcow2','-b',
            str(IMAGE_DIR/f'{self.label_prefix}-quick-prepare.qcow2'),
            str(IMAGE_DIR/f'{self.label_prefix}-build.qcow2')])
        self.state['已完成'].append('build')
        self.record(阶段='8GiB探针已编译并实测通过，原模型及YCSB不变，准备小测')

    def observe(self, tag):
        started = changed = sampled = time.monotonic()
        previous = None
        limit = 1800 if tag.startswith('smoke-') else 14400
        while time.monotonic()-started < limit:
            disks = disk_space()
            free = int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/\");print(s.f_bavail*s.f_frsize)'"))
            require(free >= 6 << 30, '运行中客机根盘低于6GiB，提前采证并关闭')
            point = json.loads(self.ssh(f'python3 {self.control}/capture_ycsb_guest.py progress '
                f'/home/femu/rrzc-sst-results/{tag} {self.target}', 60))
            sample = resource_sample(self.state.get('当前客机PID'))
            self.record(阶段=tag+'：装载/业务/全键校验', 最近观测=point,
                        资源观测=sample, 磁盘保护=disks, 客机根盘可用字节=free)
            with (self.output/'进度.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(模式=tag, **point, 资源=sample, 磁盘=disks), ensure_ascii=False)+'\n')
            require(not point['错误'], '出现后台错误，采证并关闭，不自动重跑')
            if point['完成']:
                return
            key = (point['装载完成'], int(point['最近状态'][1]) if point['最近状态'] else None,
                   point.get('校验键数', 0))
            if key != previous:
                previous, changed = key, time.monotonic()
            require(time.monotonic()-changed < 180, '180秒业务无进展，采证后正常关闭')
            if time.monotonic()-sampled >= 60:
                self.root(f'python3 {self.control}/capture_ycsb_guest.py threads '
                    f'/home/femu/rrzc-sst-results/{tag} {self.target} '
                    f'--output {self.control}/{tag}-threads-{time.time_ns()}', 30)
                sampled = time.monotonic()
            time.sleep(10)
        raise RuntimeError('超过本轮登记时限；保留错误，不自动重跑')

    def arm(self, stage, mode, analyzer):
        tag = f'{stage}-f-{mode}'
        records, ops = (8192,8192) if stage == 'smoke' else (self.record_count, OPERATIONS)
        self.parent_image = IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out = self.output/tag
        out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+tag
        error = None
        try:
            self.start(tag)
            expected = json.loads((self.output/'同库身份.json').read_text())
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),180)) == expected,
                    '实际库或新接线身份漂移')
            self.root(f'nohup setsid bash {self.target}/run_ycsb_af_guest.sh {tag} {mode} f '
                f'{records} {ops} 20261023 < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实不限速YCSB-F启动', 客机结果目录=remote, 计划业务请求数=ops)
            self.observe(tag)
        except BaseException as cause:
            error = cause
            self.record(原始业务错误=type(cause).__name__+'：'+str(cause))
            raise
        finally:
            try:
                self.finish(out, remote)
            except Exception as cleanup:
                if error is None:
                    raise
                self.record(收尾另有错误=str(cleanup))
        result = analyzer.analyze_arm(out/tag,tag,'f',mode,records,ops)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'：全键、维护、评分及关机验收通过',
                    最近结果={k:result[k] for k in ('RR次数','ZC次数','总迁移字节','前台吞吐ops每秒')})


def preflight(args):
    require(args.allow_unlocked, '须显式声明本轮未锁页')
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label), '实验标签非法')
    require(PARENT.is_file() and not running_parent(PARENT), '可复用的已扩系统盘父镜像不可用')
    require(os.access('/dev/kvm',os.R_OK|os.W_OK), 'KVM不可用')
    output = IMAGE_DIR/(args.label+'-results')
    require(not output.exists(), '拒绝覆盖旧结果')
    for tag in ('quick-prepare','build','smoke-f-space','smoke-f-model','full-f-space','full-f-model'):
        name=args.label+'-'+tag
        for p in (IMAGE_DIR/(name+'.qcow2'),Path('/tmp')/(name+'.pid'),Path('/tmp')/(name+'.qmp'),Path('/tmp')/(name+'-ssh.sock')):
            require(not p.exists(), '新目标已存在：'+str(p))
    with socket.socket() as s:
        require(s.connect_ex(('127.0.0.1',8096)) != 0, '实验端口已占用')
    binary = digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary == json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'], 'FEMU身份漂移')
    for name in ('analyze_ycsb_af.py','plot_ycsb_af.py','ycsb_af.py'):
        require(digest(ROOT/name) == digest(SOURCE/'源码'/name), '原分析或负载源已改变：'+name)
    archived = json.loads((IMAGE_DIR/'joint-ycsb32-stream-v3-20261023-results/prepare/阶段归档.json').read_text())
    require(archived['完整'] and not archived['客机仍存活'], '扩容准备父镜像未完整归档退出')
    p = profile8()
    return output,p,binary,dict(宿主=host_resources(p),磁盘=disk_space(initial=True))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=LABEL)
    parser.add_argument('--allow-unlocked',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    output,p,binary,budget=preflight(args)
    if args.check:
        print(json.dumps(dict(预检通过=True,资源=budget,设备=settings(p)['设备参数'],装载=RECORDS,业务=OPERATIONS),ensure_ascii=False,indent=2))
        return
    password=getpass.getpass('8GiB快速F配对认证（仅内存）：')
    output.mkdir()
    freeze(output,p)
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='用户确认8GiB小规模快速F配对，明日汇报；不预设收益，不替代32GiB全规模',
        FEMU二进制SHA256=binary,初始资源=budget,父镜像=str(PARENT),
        平台='8GiB/64个128MiB Zone/每区128Block；QLC、延迟与每Zone组织沿用32GiB参数',
        负载='标准语义YCSB-C++ F：50% READ、50%读改写；Zipfian、同种子、4KiB字段、不限速',
        规模=dict(装载键数=RECORDS,原始值字节=RECORDS*4096,业务操作=OPERATIONS,功能小测='各8192键/8192请求'),
        不变='同库客户端、边界第二版模型、扩展候选空间对照（非stock）、RR1024、GC20%、无WAL/缓存',
        保护='单台串行；data24GiB/系统12GiB基准余量；启动额外12GiB预算，运行提前2GiB收尾；客机根盘6GiB',
        日志='1GiB单文件上限；64KiB增量采样；失败停止并采证、哈希核对及正常关机',
        指标='API平均及P99/P99.9/P99.99、Run/前台吞吐、迁移/RR/ZC/成功Reset与Reset×128推导擦除',
        边界='单轮非稳态，P99.99尾部样本少；已结束业务维护含装载及Cleanup，独立校验另记'),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe()
        pid=os.fork()
        if pid:
            os.close(w)
            require(os.read(r,1)==b'1','监督分离失败')
            os.close(r)
            print(f'快速F监督PID={pid}，状态={output/"状态.json"}',flush=True)
            return
        os.close(r)
        os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(w,b'1');os.close(w)
    run=QuickRun(args,password,output,p)
    run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        analyzer=module_from(output/'源码/analyze_ycsb_quick.py','analyze_ycsb_quick')
        run.record(阶段='8GiB快速F准备，代码和预算已冻结')
        run.build()
        for stage in ('smoke','full'):
            for mode in LABELS:
                run.arm(stage,mode,analyzer)
            records,ops=(8192,8192) if stage=='smoke' else (RECORDS,OPERATIONS)
            pair=analyzer.compare_pair(output,stage,'f',records,ops)
            (output/f'{stage}-f-配对核验.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2))
            run.record(阶段=stage+'：F配对验收通过，客机均已关闭',最近差值=pair['差值'])
        plot=module_from(output/'源码/plot_ycsb_quick.py','plot_ycsb_quick')
        plot.render(output)
        run.record(阶段='8GiB F正式配对及图表完成，全部客机关闭',图表目录=str(output/'图表'))
    except BaseException as error:
        run.record(阶段='快速F停止，保留错误，不自动重跑',错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password=None


if __name__=='__main__':
    main()
