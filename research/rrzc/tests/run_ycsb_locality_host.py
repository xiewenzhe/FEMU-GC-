#!/usr/bin/env python3
"""单次相邻键区间先导：隔离构建、普通目录回归、Greedy业务；不自动调参或配对。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from run_tail_ablation_smoke_pair import DEVICE
from analyze_ycsb_retention import audit,independent_profile
from analyze_ycsb_backpressure import check_options
from analyze_ycsb_overlap import opportunities
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay
from check_ycsb_independent_trace import check

LABEL='ycsb-locality-20261019'
OUTPUT=IMAGE_DIR/(LABEL+'-results')
PARENT=IMAGE_DIR/'ycsb-migration-spare-20261019-build.qcow2'
IDENTITY=IMAGE_DIR/'ycsb-migration-spare-20261019-results/同库身份.json'
BUILD_IMAGE=IMAGE_DIR/(LABEL+'-build.qcow2')
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
NAME='locality-20261019'
TARGET=DIRECTORY+'/'+NAME
RESULT='/home/femu/rrzc-ycsb-results/20261017'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/name for name in ('build_ycsb_locality_guest.sh','run_ycsb_locality_guest.sh',
    'workload-locality-zns.properties','ycsb-update-range.patch',
    'probe_ycsb_progress.py','check_ycsb_independent_trace.py')]


def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'输出已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),
        '父镜像不符或仍在运行')
    require(IDENTITY.is_file(),'缺父镜像身份')
    properties=dict(s.split('=',1) for s in FILES[2].read_text().splitlines() if '=' in s and not s.startswith('#'))
    require(independent_profile(properties,1200000,'50/50',(4096,61440)),'负载与预登记范围不同')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',8096))!=0,'8096被占用')
    for mode in ('build','greedy'):
        for path in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
            Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not path.exists(),'新目标已存在：'+str(path))
    stat=os.statvfs(IMAGE_DIR)
    require(stat.f_bavail*stat.f_frsize>=24<<30,'data盘可用空间不足24GiB')
    mem=dict(row.split(':',1) for row in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')


class LocalityRun(TargetRun):
    label_prefix=LABEL
    parent_image=PARENT
    port='8096'

    def collect_target(self,output,stage):
        archive=f'{DIRECTORY}/{NAME}-evidence-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/before/ycsb" '
            f'--exclude="{NAME}/selftest-*/db" -czf {archive} -C {DIRECTORY} {NAME}',120)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(output)],120)
        self.command(['tar','-xzf',str(output/Path(archive).name),'-C',str(output)])

    def build_target(self):
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        for folder,paths in (('分析入口',(PROJECT/'research/rrzc').glob('*.py')),
                             ('宿主入口',TESTS.glob('*.py'))):
            directory=source/folder;directory.mkdir()
            self.command(['cp',*map(str,paths),str(directory)])
        (source/'FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.start('build')
        parent=json.loads(IDENTITY.read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'父镜像软件身份不符')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        self.ssh(f'test ! -e {TARGET} && test ! -e {RESULT} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        output=OUTPUT/'build';output.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_locality_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='编译可选更新范围，验证旧路径和双次相邻范围请求')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/ycsb-build.log || tail -1 {TARGET}/ycsb-build.log',900)
        finally:self.collect_target(output,'build')
        built=output/NAME
        require((built/'build.exit').read_text().strip()=='0' and (built/'selftest.exit').read_text().strip()=='0',
            '构建或真实数据库自测失败')
        log=(built/'ycsb-build.log').read_text()
        require('core/core_workload.o' in log and 'core/ycsbc.o' in log,'未确认两个消费者重编')
        software=hashes((built/'software.sha256').read_text())
        allowed={DIRECTORY+'/ycsb/ycsb',DIRECTORY+'/ycsb/core/rrzc_independent_keys.h'}
        require({n for n,d in parent.items() if software.get(n)!=d}==allowed,'软件改变超出请求范围')
        # 在临时副本独立应用冻结补丁，核验客机最终头文件；不改变宿主旧源副本。
        with tempfile.TemporaryDirectory(prefix='rrzc-locality-identity-') as tmp:
            root=Path(tmp);(root/'core').mkdir()
            header=root/'core/rrzc_independent_keys.h'
            header.write_bytes((built/'before/rrzc_independent_keys.h').read_bytes())
            require(hashlib.sha256(header.read_bytes()).hexdigest()==parent[DIRECTORY+'/ycsb/core/rrzc_independent_keys.h'],
                '客机补丁前头文件不符')
            subprocess.run(['patch','-p1','-i',str(source/'ycsb-update-range.patch')],cwd=root,check=True,capture_output=True)
            require(hashlib.sha256(header.read_bytes()).hexdigest()==software[DIRECTORY+'/ycsb/core/rrzc_independent_keys.h'],
                '客机新头文件与冻结补丁不同')
        for arm in ('first','second'):
            logs=[p.read_text(errors='replace') for p in (built/f'selftest-{arm}').glob('*.log')]
            require(any('键数=1024' in s for s in logs),'小测试全键校验缺失')
            check_options('\n'.join(logs))
            require(any('compaction_started' in s for s in logs),'小测试未实际发生压实')
            check(built/f'selftest-{arm}/audit.requests',1024,64,8192,update_range=(64,192))
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='构建及普通目录自测通过，构建客机正常关闭',当前客机PID=None)
        return software

    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<3000:
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.record(阶段='相邻范围Greedy装载、120万次业务及全键校验',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            require(not point['错误'],'出现后台错误，采证后停止，不自动重跑')
            if self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成':return
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(time.monotonic()-changed<180,'连续180秒无进展，采证后停止')
            time.sleep(15)
        raise RuntimeError('先导超过3000秒，采证后停止')

    def run_target(self):
        plan=dict(目的='检验稳定热读键与邻近更新区间是否产生多个独立Zone代际的正常选区机会',
            模式='实际Greedy；原模型只观察；V2不接入、不自动对照或调参',父镜像=str(PARENT),设备参数=DEVICE,
            唯一负载变化='更新从全库缩到[4096,65536)；热读仍为[0,4096)，读键本身不更新',
            负载='YCSB-C++派生负载，不是标准Workload A；655360个4KiB单字段，120万次50/50请求，1000次每秒，单线程种子20261017',
            不变项='8GiB、RR4096、GC20%、192MiB资格、16MiB额外预算、10秒紧迫界限、全部公共恢复开关、L0配置2/4/8、无WAL',
            验证='旧二进制与新版默认/全范围请求一致；新范围双次一致、普通目录压实及1024键校验；真实ZNS在线及655360键最新值校验',
            成功与收益分开='正确性通过不等于有机会；必须分别报告业务RR/ZC和非Greedy近期候选的不同Zone代际数',
            物理边界='256MiB是逻辑数据量，不是已确认同Zone；需用实际候选和文件日志核对，不能由逻辑相邻推出物理共置',
            决策边界='结束仅输出候选机会，不自动宣称达到配对条件；还需复核物理交集、退役以及V2回放差异',
            失败边界='业务上限3000秒，180秒无进展或错误则归档；归档成功后正常关闭本轮业务客机；不重跑',
            负对照='保留原全库更新先导和配对结果，不覆盖、不挑选只报告正结果')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        software=self.build_target()
        self.parent_image=BUILD_IMAGE;self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'业务前软件不同')
        (output/'软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_locality_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            self.collect_target(output,'run')
            if self.ssh(f'test ! -d {RESULT} || printf "存在"').strip()=='存在':self.collect(output,RESULT)
            after=self.ssh(query);(output/'软件.after.sha256').write_text(after)
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            log=point.get('维护日志路径')
            if log:
                require(Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),'日志路径不符')
                self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(output/'现场维护.log')],120)
            self.shutdown();self.record(当前客机PID=None,已归档并正常关机=True)
        require(before==after,'运行期间软件改变')
        require((output/NAME/'run.exit').read_text().strip()=='0','业务包装器未成功')
        root=output/'20261017'
        result=audit(root,'greedy',nowal=True,expected_ops=1200000,single_recovery=True,
            space_emergency=True,independent_ratio='50/50',update_range=(4096,61440))
        check_options('\n'.join(p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布']=check(root/'audit.requests',655360,4096,1200000,update_range=(4096,61440))
        lines,_=space_replay((root/'zenfs.run.log').read_text().splitlines());lines,_=single_replay(lines)
        result['正常ZC机会']=opportunities(lines)
        result['后续判定']='待复核不同Zone代际、实际物理交集与V2回放差异；不能由机会计数直接进入对照'
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'正确性或维护账目未通过')
        self.password=None
        self.record(阶段='先导及全键核验完成，客机已关闭；下一步复核物理交集和V2回放',
            当前客机PID=None,RR重置=result['RR重置'],ZC重置=result['ZC重置'],
            正常ZC轮数=result['正常ZC机会']['正常ZC决策轮数'],已完成=['greedy'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('父镜像、端口、登记负载与资源检查通过，未启动');return
    password=getpass.getpass('新隔离客机认证（仅内存）：');OUTPUT.mkdir()
    if args.detach:
        pid=os.fork()
        if pid:
            print(f'监督进程PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.setsid()
        log=os.open(OUTPUT/'监督进程.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
    run=LocalityRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    run.state.update(模式顺序=['build','greedy'],业务请求数=1200000,观察上限秒数=3000)
    try:run.run_target()
    except BaseException as error:
        run.record(阶段='本轮停止，不自动重跑；现场状态见PID与归档字段',错误=str(error) or type(error).__name__)
        raise


if __name__=='__main__':main()
