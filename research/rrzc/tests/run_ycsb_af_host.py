#!/usr/bin/env python3
"""显式获准未锁页的32GiB A至F串行对照；新目录、冻结模型、自动验收出图和关机。"""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import subprocess
import time

from run_joint_host import JointRun, PROJECT, IMAGE_DIR, OLD_DEVICE, digest_manifest
from run_tail_ablation_fixed_pair import FixedPair, running_parent
from run_rr_fair_long_v2_host import CollectedRun
from finerr_profile import load, settings
from ycsb_af import RECORDS, OPERATIONS, WORKLOADS, LABELS, require, digest
from analyze_ycsb_af import analyze_arm, compare_pair

ROOT = PROJECT/'research/rrzc'
TESTS = ROOT/'tests'
LABEL = 'joint-ycsb32-af-v3-20261023'
# 使用同一已验收模型的更干净父镜像，不降低6GiB根盘保护来容纳重复客户端。
SOURCE = IMAGE_DIR/'joint-terminal-v2-20261023-results'
PARENT = IMAGE_DIR/'joint-terminal-v2-20261023-build.qcow2'
MANIFEST = SOURCE/'同库身份.json'
FILES = [TESTS/n for n in ('build_ycsb_af_guest.sh', 'run_ycsb_af_guest.sh',
         'rrzc_ycsb_suite.h', 'ycsb-suite-observation.patch', 'suite_device_probe.cc',
         'strict_stats_guest.py', 'workload-stable-unlimited.properties', 'suite_verify_ycsb.cc',
         'ycsb-phase-timing.patch')]
FILES += [ROOT/'ycsb_af.py', ROOT/'model/rrzc_client_phase.h']


def host_resources(profile):
    """只豁免用户已批准的锁页额度；内存、磁盘、串行及所有权检查仍保留。"""
    reply = subprocess.run(['ps', '-C', 'qemu-system-x86_64', '-o', 'pid=,user=,rss=,args='],
                           capture_output=True, text=True, check=False)
    require(reply.returncode in (0, 1), '不能核验宿主虚拟机')
    own = [s for s in reply.stdout.splitlines() if str(IMAGE_DIR)+'/' in s or
           str(PROJECT/'build-femu/qemu-system-x86_64') in s]
    require(not own, '本项目仍有客机，不叠加：' + '\n'.join(own))
    memory = dict(s.split(':', 1) for s in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0])*1024
    required = profile['命名空间']['容量字节'] + (profile['沿用条件']['客机内存GiB'] << 30) + (16 << 30)
    require(available >= required, '内存不足NAND、客机与16GiB余量，停止不降门槛')
    stat = os.statvfs(IMAGE_DIR)
    free = stat.f_bavail*stat.f_frsize
    require(free >= 24 << 30, 'data盘余量不足24GiB，不启动新组')
    return dict(可用内存字节=available, 保护所需字节=required, data盘可用字节=free,
                宿主锁页额度=list(resource.getrlimit(resource.RLIMIT_MEMLOCK)),
                本轮锁页='用户明确批准：仅新QEMU子进程锁页软硬额度设0；不改系统及旧入口')


def resource_sample(pid):
    vmstat = dict(s.split() for s in Path('/proc/vmstat').read_text().splitlines())
    out = dict(UTC=datetime.now(timezone.utc).isoformat(), 宿主换入页=int(vmstat['pswpin']),
               宿主换出页=int(vmstat['pswpout']), PID=pid)
    if pid and Path(f'/proc/{pid}/status').exists():
        status = dict(s.split(':', 1) for s in Path(f'/proc/{pid}/status').read_text().splitlines())
        for k in ('VmRSS', 'VmLck', 'VmSwap', 'VmSize'):
            out[k] = status.get(k, '').strip()
        require(out['VmLck'] == '0 kB', '本轮应明确未锁页，实际状态不符')
        raw = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        out.update(次缺页=int(raw[7]), 主缺页=int(raw[9]))
    return out


class SuiteRun(JointRun):
    close_guest = CollectedRun.close_guest
    finish = CollectedRun.finish

    def __init__(self, *args):
        super().__init__(*args)
        self.parent_image = PARENT
        self.deployed = False
        self.state.update(边界='32GiB、显式未锁页；A至F单轮配对，不宣称稳态或稳定因果收益',
                          模式顺序=[f'{s}-{w}-{m}' for s in ('smoke', 'full') for w in WORKLOADS for m in LABELS],
                          锁页方式='仅本轮QEMU禁用；其他资源保护不变', 已完成=[])

    def command(self, argv, timeout=60):
        if argv and str(argv[0]).endswith('qemu-system-x86_64'):
            require(argv.count(OLD_DEVICE) == 1, '继承启动器的设备标记漂移')
            argv = [settings(self.profile)['设备参数']+',zns_rr_strict=on' if s == OLD_DEVICE else s for s in argv]
            def unlocked():
                resource.setrlimit(resource.RLIMIT_MEMLOCK, (0, 0))
            result = subprocess.run(argv, cwd=PROJECT, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=180, preexec_fn=unlocked)
            with (self.output/'管理命令.log').open('a') as stream:
                stream.write(json.dumps(argv, ensure_ascii=False)+'\n'+result.stdout+'\n')
            require(result.returncode == 0, '32GiB QEMU启动失败，保留管理命令.log')
            return result.stdout
        return FixedPair.command(self, argv, timeout)

    def start(self, mode):
        self.record(启动前资源=host_resources(self.profile), 阶段=mode+'准备启动')
        require(digest(PROJECT/'build-femu/qemu-system-x86_64') == self.qemu_digest, 'FEMU二进制漂移')
        self.deployed = False
        self.control = self.target+'-evidence-'+mode
        self.state.update(采证目录=self.control, 当前阶段=mode)
        try:
            FixedPair.start(self, mode)
        except BaseException:
            # 若daemonize成功但SSH失败，也必须登记精确PID供finally正常关机。
            pidfile = Path(f'/tmp/{self.label_prefix}-{mode}.pid')
            image = IMAGE_DIR/f'{self.label_prefix}-{mode}.qcow2'
            if pidfile.exists():
                pid = int(pidfile.read_text())
                if Path(f'/proc/{pid}/cmdline').exists() and str(image).encode() in Path(f'/proc/{pid}/cmdline').read_bytes():
                    self.record(当前客机PID=pid, 当前镜像=str(image))
            raise
        self.record(QMP=str(self.qmp), 当前阶段=mode, 资源观测=resource_sample(self.state['当前客机PID']))
        self.ssh(f'test ! -e {self.control} && mkdir {self.control}')
        for local, remote in (('capture_ycsb_guest.py', 'capture_ycsb_core.py'),
                              ('capture_ycsb_af_guest.py', 'capture_ycsb_guest.py')):
            source = self.output/'源码'/local
            self.command(self.scp()+[str(source), f'femu@127.0.0.1:{self.control}/{remote}'])
            require(self.ssh(f'sha256sum {self.control}/{remote}').split()[0] == digest(source), '采证接线哈希不符')
        self.deployed = True

    def collect(self, out, remote):
        if remote == self.target:
            name = Path(remote).name
            present = self.ssh(f'if test -d {remote}; then printf "存在"; else printf "未创建"; fi')
            if present == '未创建':
                (out/'构建目录未创建.json').write_text(json.dumps(dict(目录=remote,
                    说明='身份或资源门禁在创建构建目录之前拒绝，原始管理日志和可用采证仍保留'), ensure_ascii=False))
                return
            archive = f'{remote}-{self.state["当前阶段"]}-evidence.tar.gz'
            self.ssh(f'test ! -e {archive} && tar --exclude="{name}/ycsb" '
                     f'--exclude="{name}/selftest-*/db" -czf {archive} -C /home/femu {name}', 180)
            expected = self.ssh(f'sha256sum {archive}').split()[0]
            self.command(self.scp()+[f'femu@127.0.0.1:{archive}', str(out)], 180)
            require(digest(out/Path(archive).name) == expected, '构建采证包哈希错误')
            self.command(['tar', '-xzf', str(out/Path(archive).name), '-C', str(out)], 120)
            return
        expected = digest_manifest(self.ssh(f'find {remote} -type f -exec sha256sum {{}} +', 300))
        require(expected, '没有可归档文件：'+remote)
        JointRun.collect(self, out, remote)
        folder = out/Path(remote).name
        wanted = {str(Path(p).relative_to(remote)): h for p, h in expected.items()}
        actual = {str(p.relative_to(folder)): digest(p) for p in folder.rglob('*') if p.is_file()}
        require(actual == wanted, '归档文件集合或哈希不符：'+remote)
        (out/(folder.name+'.归档核验.json')).write_text(json.dumps(actual, ensure_ascii=False, indent=2))

    def build(self):
        frozen = self.output/'源码'
        frozen.mkdir()
        dependencies = [Path(__file__), ROOT/'analyze_ycsb_af.py', ROOT/'plot_ycsb_af.py',
                        TESTS/'capture_ycsb_guest.py', TESTS/'capture_ycsb_af_guest.py']
        for source in FILES+dependencies:
            (frozen/source.name).write_bytes(source.read_bytes())
        analysis = frozen/'分析'
        analysis.mkdir()
        for p in ROOT.glob('*.py'):
            (analysis/p.name).write_bytes(p.read_bytes())
        parent = json.loads(MANIFEST.read_text())
        out = self.output/'build'
        out.mkdir()
        error = None
        try:
            self.start('build')
            free = int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            self.record(构建根盘可用字节=free)
            require(free >= 6 << 30, '构建客机根盘不足6GiB')
            raw = self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent), 180)
            require(digest_manifest(raw) == parent, '父库、模型或旧客户端身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[
                str(frozen/'parent.software.sha256'), f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_ycsb_af_guest.sh '
                     f'< /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='32GiB隔离构建，A至F双次普通目录数据与统计验收；模型库不变')
            self.wait_for(self.target+'/build.finished',
                          f'test ! -f {self.target}/ycsb-build.log || tail -1 {self.target}/ycsb-build.log', 1800)
        except BaseException as cause:
            error = cause
            self.record(原始构建错误=type(cause).__name__+'：'+str(cause))
            raise
        finally:
            try:
                self.finish(out)
            except Exception as cleanup:
                if error is None:
                    raise
                self.record(构建收尾另有错误=str(cleanup))
        built = out/Path(self.target).name
        require(all((built/n).read_text().strip() == '0' for n in ('build.exit', 'selftest.exit')),
                '构建或普通目录A至F验收失败，停止，不继续真实盘')
        software = digest_manifest((built/'software.sha256').read_text())
        require(all(software.get(n) == v for n, v in parent.items()), '不应修改父库或旧客户端')
        results = json.loads((built/'普通目录验收.json').read_text())
        require(set(results) == set(WORKLOADS) and all(len(v) == 2 for v in results.values()), '普通目录双次验收不完整')
        (self.output/'同库身份.json').write_text(json.dumps(software, ensure_ascii=False, indent=2))
        self.state['已完成'].append('build')
        self.record(阶段='新客户端编译及A至F双次普通目录验收通过；构建客机已归档关闭')

    def observe(self, tag):
        started = changed = sampled = time.monotonic()
        previous = None
        # 正式盘包括24GiB装载、扫描和独立校验；时限不改变负载请求速率。
        limit = 1800 if tag.startswith('smoke-') else 21600
        while time.monotonic()-started < limit:
            point = json.loads(self.ssh(f'python3 {self.control}/capture_ycsb_guest.py progress '
                                       f'/home/femu/rrzc-sst-results/{tag} {self.target}', 60))
            sample = resource_sample(self.state.get('当前客机PID'))
            self.record(阶段=tag+'真实业务及全键校验', 最近观测=point, 资源观测=sample)
            with (self.output/'进度.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(模式=tag, **point, 资源=sample), ensure_ascii=False)+'\n')
            require(not point['错误'], '出现后台错误，采证归档并正常关机')
            if point['完成']:
                return
            key = (point['装载完成'], int(point['最近状态'][1]) if point['最近状态'] else None,
                   point.get('校验键数', 0))
            if key != previous:
                previous, changed = key, time.monotonic()
            require(time.monotonic()-changed < 180, '业务连续180秒无进展，采证后正常关机')
            if time.monotonic()-sampled >= 60:
                self.root(f'python3 {self.control}/capture_ycsb_guest.py threads '
                          f'/home/femu/rrzc-sst-results/{tag} {self.target} '
                          f'--output {self.control}/{tag}-threads-{time.time_ns()}', 30)
                sampled = time.monotonic()
            time.sleep(10)
        raise RuntimeError('本组达到登记时限；保留错误与日志并正常关机，不自动重跑')

    def arm(self, stage, letter, mode):
        tag = f'{stage}-{letter}-{mode}'
        records, ops = (8192, 8192) if stage == 'smoke' else (RECORDS, OPERATIONS)
        self.parent_image = IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out = self.output/tag
        out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+tag
        expected = json.loads((self.output/'同库身份.json').read_text())
        error = None
        try:
            self.start(tag)
            actual = digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected), 180))
            require(actual == expected, '运行前冻结库、模型或客户端身份不符')
            self.root(f'nohup setsid bash {self.target}/run_ycsb_af_guest.sh {tag} {mode} {letter} '
                      f'{records} {ops} 20261023 < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'不限速真实YCSB，开始监督', 客机结果目录=remote, 计划业务请求数=ops)
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
        result = analyze_arm(out/tag, tag, letter, mode, records, ops)
        (out/'独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'数据、统计、实际评分、归档及正常关机核验通过',
                    最近结果={k: result[k] for k in ('RR次数', 'ZC次数', '总迁移字节', '前台吞吐ops每秒')})


def preflight(args):
    require(args.allow_unlocked, '必须显式传入--allow-unlocked，旧入口仍保持锁页保护')
    require(re.fullmatch(r'joint-[a-z0-9-]+', args.label), '实验标签非法')
    require(PARENT.is_file() and MANIFEST.is_file() and not running_parent(PARENT), '已停止父镜像或身份缺失')
    require(os.access('/dev/kvm', os.R_OK | os.W_OK), '当前进程无KVM权限')
    profile = load()
    require(profile['命名空间']['容量字节'] == 32 << 30 and profile['命名空间']['Zone数量'] == 256,
            '本轮只允许论文32GiB参数源')
    output = IMAGE_DIR/(args.label+'-results')
    require(not output.exists(), '拒绝覆盖结果或自动重跑')
    state = json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None and 'build' in state['已完成'], '父实验未正常退出')
    archive = json.loads((SOURCE/'build/阶段归档.json').read_text())
    require(archive['完整'] and not archive['客机仍存活'], '父构建归档不完整')
    binary = digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary == json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'], '严格FEMU版本漂移')
    for tag in ['build', *[f'{s}-{w}-{m}' for s in ('smoke', 'full') for w in WORKLOADS for m in LABELS]]:
        name = args.label+'-'+tag
        for path in (IMAGE_DIR/(name+'.qcow2'), Path('/tmp')/(name+'.pid'),
                     Path('/tmp')/(name+'.qmp'), Path('/tmp')/(name+'-ssh.sock')):
            require(not path.exists(), '目标已存在：'+str(path))
    with socket.socket() as probe:
        require(probe.connect_ex(('127.0.0.1', 8096)) != 0, '8096端口占用')
    return output, profile, binary, host_resources(profile)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label', default=LABEL)
    p.add_argument('--allow-unlocked', action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--detach', action='store_true')
    a = p.parse_args()
    output, profile, binary, resources = preflight(a)
    if a.check:
        print(json.dumps(dict(只读预检通过=True, 资源=resources, 装载键数=RECORDS,
                              每组业务请求=OPERATIONS), ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('32GiB未锁页A至F实验认证（仅驻留内存）：')
    output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='用标准语义A至F的新实测生成汇报柱状图，不预设正收益', 父镜像=str(PARENT),
        FEMU二进制SHA256=binary, 初始资源=resources, 单台串行=True,
        锁页='用户明确批准不锁页；仅本轮QEMU子进程软硬额度置0，原保护及系统配置不变',
        平台='32GiB/256个128MiB Zone/128块每区，严格RR1024、GC20%、额外回收门槛0',
        负载='保留当前公开YCSB-C++源码中A至F的比例、Zipfian/latest和扫描生成器；关闭自定义独立选键',
        适配='单线程、同种子20261023、单字段4096字节、无WAL/块缓存、不限速；不是Java YCSB默认字段布局',
        策略={'space': '同扩展候选空间控制，关闭RR/留存评分，非stock Greedy',
              'model': '已验收第二版边界RR-aware，未接入新支持预测核'},
        规模={'普通目录': 'A至F各双次1024键/8192请求并全键校验',
              '功能': 'A至F两策略各8192键/8192请求', '正式首轮': f'每组{RECORDS}键/{OPERATIONS}请求，单字段原始值24GiB'},
        口径='总维护含装载、Run与Cleanup；独立校验单列；API非裸IO；F额外记录完整事务，不能重复计业务数',
        指标='平均和P99/P99.9/P99.99、前台与Run吞吐、总迁移及RR/ZC拆分、RR/ZC、成功Reset与推导块擦除',
        保护='内存保留16GiB余量/data保留24GiB/单组无请求进展180秒/成功失败均采证哈希归档及正常关机',
        图表='正式配对验收后自动生成PNG/PDF/SVG和CSV，标明未锁页、单轮、不适用指标不记零',
        限制='宿主换页及缺页可干扰延迟；不宣称稳态或稳定因果，纯读无ZC不强行制造机会'), ensure_ascii=False, indent=2))
    if a.detach:
        r, w = os.pipe()
        pid = os.fork()
        if pid:
            os.close(w)
            require(os.read(r, 1) == b'1', '监督分离失败')
            os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}', flush=True)
            return
        os.close(r)
        os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(w, b'1')
        os.close(w)
    run = SuiteRun(a, password, output, profile)
    run.qemu_digest = binary
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        run.build()
        for stage in ('smoke', 'full'):
            for index, letter in enumerate(WORKLOADS):
                # 顺序轮换避免每种负载都固定由同一策略先跑；仍严格串行。
                modes = list(LABELS) if index % 2 == 0 else list(reversed(LABELS))
                for mode in modes:
                    run.arm(stage, letter, mode)
                records, ops = (8192, 8192) if stage == 'smoke' else (RECORDS, OPERATIONS)
                result = compare_pair(output, stage, letter, records, ops)
                (output/f'{stage}-{letter}-配对核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                run.record(阶段=stage+'-'+letter+'配对完整通过，全部客机已关闭', 最近差值=result['差值'])
                if stage == 'full':
                    from plot_ycsb_af import render
                    render(output)
        run.record(阶段='A至F首轮全部完成，柱状图与CSV已生成，全部客机已关闭', 图表目录=str(output/'图表'))
    except BaseException as error:
        run.record(阶段='实验停止，原始错误已保留，不自动重跑', 错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
