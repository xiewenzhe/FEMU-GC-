#!/usr/bin/env python3
"""隔离构建读写独立派生负载，仅运行Greedy先导，不自动对照或重跑。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun as ReadPilotRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_retention import audit
from analyze_ycsb_backpressure import check_options
from analyze_ycsb_overlap import opportunities
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay
from check_ycsb_independent_trace import check

LABEL='ycsb-independent-20261018'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-backpressure-20261018-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/independent-20261018'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
TESTS=PROJECT/'research/rrzc/tests'
PORT='8096'
FILES=[TESTS/name for name in ('build_ycsb_independent_guest.sh','run_ycsb_independent_guest.sh',
    'workload-independent-zns.properties','ycsb-independent-keys.patch',
    'probe_ycsb_progress.py','check_ycsb_independent_trace.py')]


def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'输出已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),
        '父镜像不符或仍在运行')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8096被占用')
    for mode in ('build','greedy'):
        for path in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
            Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not path.exists(),'新目标已存在：'+str(path))
    # 本轮不复制客机测试数据库或旧二进制，归档量受单文件256MiB及单轮请求上限约束。
    for path,minimum in ((IMAGE_DIR,12<<30),(PROJECT,2<<30)):
        stat=os.statvfs(path);require(stat.f_bavail*stat.f_frsize>=minimum,'磁盘余量不足')
    mem=dict(row.split(':',1) for row in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')


class IndependentRun(ReadPilotRun):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT

    def collect_target(self,output,stage):
        archive=f'{DIRECTORY}/independent-20261018-evidence-{stage}.tar.gz'
        # 测试数据库和原版大二进制仍保存在子镜像；宿主取回源码、日志、哈希和请求证据。
        self.ssh(f'test ! -e {archive} && tar --exclude="independent-20261018/before/ycsb" '
            f'--exclude="independent-20261018/selftest-*/db" --exclude="independent-20261018/evidence-*.tar.gz" '
            f'-czf {archive} -C {DIRECTORY} independent-20261018',120)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(output)],120)
        self.command(['tar','-xzf',str(output/Path(archive).name),'-C',str(output)])

    def build_target(self):
        if getattr(self,'resume_build',False):
            output=OUTPUT/'build-recovery';output.mkdir()
            self.collect_target(output,'build-recovery')
            parent=json.loads((PROJECT/'research/rrzc/results/ycsb-backpressure-20261018/同库身份.json').read_text())
            return self.verify_built(output,parent)
        plan=dict(目的='同时形成热读和更新空间压力，先验证RR/ZC及正常合格候选，不宣称模型收益',
            代码范围='默认关闭的YCSB-C++读写独立选键；原生生成器不改，FEMU/ZenFS/绑定/静态库不改',
            负载='装载655360个4KiB单字段；80%读首4096键原生ScrambledZipfian0.99，20%全空间均匀更新；单线程固定种子20261017',
            请求数=1200000,速率上限=1000,模式='实际Greedy，模型只观察；不是标准公开Workload',
            不变项='8GiB、RR4096、GC20%、192MiB资格、16MiB额外预算、公共恢复及对齐开关、L0触发2/减速4/暂停8、无WAL、无块缓存',
            验证='本地原版逐字节兼容；新客机原二进制与新版关闭路径、双次启用请求一致、实际压实、1024键最新值；业务在线校验及结束655360键全量校验',
            机会='业务RR和ZC均大于0，且正常GC含非Greedy临RR合格候选和影子改选；装载维护不能充当业务机会',
            边界='不保证同Zone放置，不使用未来失效信息；没有机会则如实报告，不自动调阈值/重跑/四组对照',
            观察上限秒=3000,无进展上限秒=180,宿主归档='不重复复制客机普通目录数据库和原版二进制，保存在独立镜像')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        self.start('build')
        parent=json.loads((PROJECT/'research/rrzc/results/ycsb-backpressure-20261018/同库身份.json').read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'停止父镜像软件身份不符')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        output=OUTPUT/'build';output.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_independent_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='编译请求扩展与原版兼容、双次真实数据库自测')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/ycsb-build.log || tail -1 {TARGET}/ycsb-build.log',900)
        finally:self.collect_target(output,'build')
        return self.verify_built(output,parent)

    def verify_built(self,output,parent):
        built=output/'independent-20261018'
        require((built/'build.exit').read_text().strip()=='0' and (built/'selftest.exit').read_text().strip()=='0',
            '构建或真实数据库自测失败')
        log=(built/'ycsb-build.log').read_text()
        require('core/core_workload.o' in log and 'core/ycsbc.o' in log,'未确认两个类布局消费者重编')
        software=hashes((built/'software.sha256').read_text())
        require({n for n,d in parent.items() if software.get(n)!=d}=={DIRECTORY+'/ycsb/ycsb'},
            '父软件身份变化超出YCSB二进制')
        for name in ('core_workload.cc','core_workload.h','rrzc_independent_keys.h'):
            frozen=PROJECT/'build-femu/rrzc/independent-keys-work/core'/name
            require(software[DIRECTORY+'/ycsb/core/'+name]==hashlib.sha256(frozen.read_bytes()).hexdigest(),
                '实际工作负载源码与冻结副本不同：'+name)
        for arm in ('first','second'):
            logs=[p.read_text(errors='replace') for p in (built/f'selftest-{arm}').glob('*.log')]
            require(any('键数=1024' in s for s in logs),'小测试全键校验缺失')
            check_options('\n'.join(logs))
            require(any('compaction_started' in s for s in logs),'小测试未实际发生压实')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='构建和真实数据库自测通过，构建客机正常关闭',当前客机PID=None)
        return software

    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<3000:
            done=self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成'
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.record(阶段='读写独立分布120万次Greedy验证',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(not point['错误'],'出现后台错误，采证保留现场，不自动重跑')
            if done:return
            require(time.monotonic()-changed<180,'连续180秒无进展，采证保留现场')
            time.sleep(15)
        raise RuntimeError('有界先导超时，采证保留现场')

    def run_target(self):
        software=self.build_target()
        self.parent_image=BUILD_IMAGE;self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'业务前软件身份不同')
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_independent_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.collect(output,RESULT);self.collect_target(output,'run')
            after=self.ssh(query);(output/'软件.after.sha256').write_text(after)
            log=point.get('维护日志路径')
            require(log and Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),
                '未确认唯一维护日志，不猜测归档')
            self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(output/'现场维护.log')],120)
        require(before==after,'业务期间软件身份改变')
        root=output/'20261017'
        result=audit(root,'greedy',nowal=True,expected_ops=1200000,single_recovery=True,space_emergency=True)
        check_options('\n'.join(p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布']=check(root/'audit.requests',655360,4096,1200000)
        lines,_=space_replay((root/'zenfs.run.log').read_text().splitlines());lines,_=single_replay(lines)
        result['正常ZC机会']=opportunities(lines)
        phases=result['分阶段维护'];business=phases['业务']
        no_load=all(phases[s][k]==0 for s in ('装载及等待','交界一秒不确定') for k in ('ZC尝试','RR尝试'))
        result['具备进入机制配对条件']=(result['验收通过'] and no_load and business['RR重置']>0
            and business['ZC重置']>0 and result['正常ZC机会']['包含非Greedy临RR候选轮数']>0
            and result['正常ZC机会']['影子会改选轮数']>0)
        result['机会时间边界']='只在装载及交界无维护时用全日志机会代表业务机会；否则暂不判定可配对'
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'正确性或维护账目未通过')
        self.shutdown();self.record(阶段='独立分布先导及全键校验通过，业务客机正常关闭',当前客机PID=None,
            RR重置=business['RR重置'],ZC重置=business['ZC重置'],具备进入机制配对条件=result['具备进入机制配对条件'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    parser.add_argument('--resume-build',action='store_true',help='只恢复已完成自测但归档失败的同一构建，不重跑业务')
    args=parser.parse_args()
    previous=None
    if args.resume_build:
        previous=json.loads((OUTPUT/'状态.json').read_text())
        require(previous['当前镜像']==str(BUILD_IMAGE) and previous['阶段']=='独立分布先导失败，保留现场'
            and previous.get('错误')=='命令失败，退出1；详见管理命令.log', '不是待恢复的构建归档错误')
        pid=previous['当前客机PID']
        require(f'file={BUILD_IMAGE},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode() in
            Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),'构建客机身份改变')
        require(not (IMAGE_DIR/f'{LABEL}-greedy.qcow2').exists() and not (OUTPUT/'greedy').exists()
            and not (OUTPUT/'build-recovery').exists(),'已有业务或恢复结果，拒绝重跑')
    else:preflight()
    if args.check:print('隔离父镜像、端口和资源检查通过，未启动');return
    password=getpass.getpass('本轮隔离客机认证（仅内存）：')
    if previous is None:OUTPUT.mkdir()
    run=IndependentRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    run.state.update(模式顺序=['build','greedy'],业务请求数=1200000,观察上限秒数=3000)
    if previous is not None:
        (OUTPUT/'归档失败状态原件.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2))
        run.state=previous;run.state['监督进程PID']=os.getpid();run.state.pop('错误',None)
        run.socket=f'/tmp/{LABEL}-build-ssh.sock';run.qmp=Path(f'/tmp/{LABEL}-build.qmp')
        run.resume_build=True
        run.record(阶段='修正归档输出位置后复核已完成自测，不重编不重跑',原归档错误已保留=True)
        source=OUTPUT/'实验源/恢复后的宿主控制器.py'
        run.command(['cp',str(Path(__file__).resolve()),str(source)])
    try:run.run_target()
    except BaseException as error:
        run.record(阶段='独立分布先导失败，保留现场',错误=str(error) or type(error).__name__);raise


if __name__=='__main__':main()
