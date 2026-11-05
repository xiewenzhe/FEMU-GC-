#!/usr/bin/env python3
"""同库扩大相邻更新范围的一次Greedy先导；不改模型，不重编或自动重跑。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from run_tail_ablation_smoke_pair import DEVICE
from analyze_ycsb_retention import audit,independent_profile,sha
from analyze_ycsb_locality import gc_checks
from analyze_ycsb_overlap import opportunities
from analyze_ycsb_backpressure import check_options
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay
from check_ycsb_independent_trace import check

LABEL='ycsb-locality-wide-20261019'
NAME='locality-wide-20261019'
OUTPUT=IMAGE_DIR/(LABEL+'-results')
PARENT=IMAGE_DIR/'ycsb-locality-20261019-build.qcow2'
PREVIOUS=IMAGE_DIR/'ycsb-locality-20261019-results'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/'+NAME
RESULT='/home/femu/rrzc-ycsb-results/20261017'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('run_ycsb_locality_wide_guest.sh','check_ycsb_locality_wide_guest.sh',
    'workload-locality-wide-zns.properties','probe_ycsb_progress.py','check_ycsb_independent_trace.py')]


def properties(path):
    return dict(s.split('=',1) for s in path.read_text().splitlines() if '=' in s and not s.startswith('#'))


def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'结果已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'父镜像不符或运行中')
    previous=json.loads((PREVIOUS/'greedy/独立核验.json').read_text())
    require(previous['验收通过'],'上一轮基础正确性未通过')
    old=properties(PREVIOUS/'greedy/20261017/workload.properties');new=properties(FILES[2])
    require({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}=={'rrzc.update_key_count'},'配置变化超出更新键数')
    require(independent_profile(new,1200000,'50/50',(4096,258048)),'不是登记的扩大范围')
    require(sha(PROJECT/'build-femu/qemu-system-x86_64')==(PREVIOUS/'实验源/FEMU.sha256').read_text().split()[0],
        'FEMU与上一轮不同')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',8096))!=0,'8096被占用')
    for path in (IMAGE_DIR/f'{LABEL}-greedy.qcow2',Path(f'/tmp/{LABEL}-greedy.pid'),
        Path(f'/tmp/{LABEL}-greedy.qmp'),Path(f'/tmp/{LABEL}-greedy-ssh.sock')):
        require(not path.exists(),'目标已存在：'+str(path))
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=16<<30,'data盘可用空间不足16GiB')
    mem=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')


class WideRun(TargetRun):
    label_prefix=LABEL
    parent_image=PARENT
    port='8096'

    def collect_target(self,output,stage):
        archive=f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/selftest-*/db" -czf {archive} -C {DIRECTORY} {NAME}',120)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(output)],120)
        self.command(['tar','-xzf',str(output/Path(archive).name),'-C',str(output)])

    def run_target(self):
        plan=dict(目的='单次扩大相邻范围，观察原GC触发与RR风险是否同时存在；不预设正结果',
            父镜像=str(PARENT),设备参数=DEVICE,模式='实际Greedy，原模型只观察，V2不接入',
            唯一业务配置变化='更新键数61440变258048，范围[4096,262144)；读仍为[0,4096)',
            范围解释='1008MiB更新字段加16MiB热读字段，共1GiB逻辑范围；不保证同Zone',
            不变项='同FEMU、同YCSB二进制和静态库；8GiB、RR4096、GC20%、192MiB资格、16MiB预算、10秒紧迫界限、公共恢复、L0配置2/4/8、无WAL',
            请求='655360键、4KiB单字段；120万次读写各半、1000次每秒、单线程种子20261017；派生YCSB-C++，不是标准Workload A',
            小测试='新子镜像普通目录双次2560键、16热读键、[16,1024)更新、8192次请求，范围按业务键数同比缩小256倍；请求与最新值逐字节核对',
            结果指标='GC入口、业务RR/ZC、总迁移、平均及p95/p99延迟、Run吞吐量、正常非Greedy近期机会',
            吞吐口径='Run含请求生成、审计、限速及Cleanup，不含装载、装载后等待和独立全键校验；限速1000ops/s，不是饱和吞吐',
            时间界限='最多3000秒，180秒无进展或后台错误则采证；业务归档成功后正常关机，不自动重跑',
            后续边界='仅此一次先导，保留两个旧负载；有ZC和RR还不等于有改选机会，需核验不同Zone代际再决定V2对照')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        for folder,paths in (('分析入口',(PROJECT/'research/rrzc').glob('*.py')),('宿主入口',TESTS.glob('*.py'))):
            directory=source/folder;directory.mkdir();self.command(['cp',*map(str,paths),str(directory)])
        (source/'FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        expected=json.loads((PREVIOUS/'同库身份.json').read_text())
        self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        before=self.ssh(query);require(hashes(before)==expected,'客机软件与上轮不同')
        (output/'软件.before.sha256').write_text(before)
        self.ssh(f'test ! -e {TARGET} && test ! -e {RESULT} && mkdir {TARGET}')
        manifest=source/'software.sha256'
        manifest.write_text(before+''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+TARGET+'/'+p.name+'\n' for p in FILES))
        (OUTPUT/'同库身份.json').write_text(json.dumps(hashes(manifest.read_text()),ensure_ascii=False,indent=2))
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        small=output/'selftest';small.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/check_ycsb_locality_wide_guest.sh < /dev/null > {TARGET}/selftest-launcher.log 2>&1 &')
            self.record(阶段='同库扩大范围：普通目录双次请求及全键回归')
            self.wait_for(f'{TARGET}/selftest.finished',f'test ! -f {TARGET}/selftest.exit || cat {TARGET}/selftest.exit',300)
        finally:self.collect_target(small,'selftest')
        require((small/NAME/'selftest.exit').read_text().strip()=='0','小测试失败，保留现场，不启动业务')
        for arm in ('first','second'):
            root=small/NAME/f'selftest-{arm}'
            require('键数=2560' in (root/'verify.log').read_text(),'缺小测试全键校验')
            check(root/'audit.requests',2560,16,8192,update_range=(16,1008))
        require(self.ssh(query)==before,'小测试期间软件改变')
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_locality_wide_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            start=changed=time.monotonic();last=None
            while time.monotonic()-start<3000:
                point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
                self.record(阶段='1GiB范围Greedy装载、120万次业务及全键校验',最近观测=point)
                with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
                require(not point['错误'],'后台错误，归档后正常关闭，不重跑')
                if self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成':break
                key=(point['阶段'],point['操作数'])
                if key!=last:last=key;changed=time.monotonic()
                require(time.monotonic()-changed<180,'连续180秒无进展')
                time.sleep(15)
            else:raise RuntimeError('超过3000秒观察上限')
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
        require(before==after,'业务期间软件改变')
        require((output/NAME/'run.exit').read_text().strip()=='0','业务包装器未成功')
        root=output/'20261017'
        result=audit(root,'greedy',nowal=True,expected_ops=1200000,single_recovery=True,
            space_emergency=True,independent_ratio='50/50',update_range=(4096,258048))
        check_options('\n'.join(p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布']=check(root/'audit.requests',655360,4096,1200000,update_range=(4096,258048))
        text=(root/'zenfs.run.log').read_text();result['GC入口']=gc_checks(text)
        lines,_=space_replay(text.splitlines());lines,_=single_replay(lines)
        result['正常ZC机会']=opportunities(lines)
        result['后续判定']='待核验不同Zone代际、物理交集及V2回放；不自动进入模型对照'
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'正确性或维护账目未通过')
        self.password=None
        self.record(阶段='1GiB先导及全键核验完成，已正常关机；等待机会分析',当前客机PID=None,
            RR重置=result['RR重置'],ZC重置=result['ZC重置'],吞吐量=result['吞吐量'],
            正常ZC轮数=result['正常ZC机会']['正常ZC决策轮数'],已完成=['greedy'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('父镜像、同程序、唯一配置差异、端口和资源检查通过，未启动');return
    password=getpass.getpass('隔离客机认证（仅内存）：');OUTPUT.mkdir()
    if args.detach:
        pid=os.fork()
        if pid:print(f'监督进程PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.setsid()
        log=os.open(OUTPUT/'监督进程.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
    run=WideRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    run.state.update(模式顺序=['greedy'],业务请求数=1200000,观察上限秒数=3000)
    try:run.run_target()
    except BaseException as error:
        run.record(阶段='本轮停止，不自动重跑；客机状态见PID和归档字段',错误=str(error) or type(error).__name__)
        raise


if __name__=='__main__':main()
