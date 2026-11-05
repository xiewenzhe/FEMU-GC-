#!/usr/bin/env python3
"""同构建镜像串行YCSB收益预算对照；失败归档关机，不自动重跑。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_benefit_device_host import DeviceRun
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_tail_ablation_fixed_pair import running_parent
from run_tail_ablation_smoke_pair import DEVICE
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_retention import independent_profile,sha
from analyze_ycsb_benefit_pair import audit_run,compare,MODES
from analyze_ycsb_backpressure import check_options
from check_ycsb_independent_trace import check

LABEL='ycsb-benefit-pair-20261019'
NAME='benefit-pair-20261019'
OUTPUT=IMAGE_DIR/(LABEL+'-results')
PARENT=IMAGE_DIR/'benefit-device-20261019-build.qcow2'
BUILD_RESULT=IMAGE_DIR/'benefit-device-20261019-results'
PILOT=IMAGE_DIR/'ycsb-locality-wide-20261019-results'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/'+NAME
RESULT='/home/femu/rrzc-ycsb-results/20261017'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('run_ycsb_benefit_pair_guest.sh','check_ycsb_benefit_pair_guest.sh',
    'workload-locality-wide-zns.properties','probe_ycsb_progress.py','check_ycsb_independent_trace.py')]


def resources():
    memory=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=24<<30,'data盘可用空间不足24GiB')


def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'结果已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'构建父镜像不存在或仍运行中')
    require(json.loads((BUILD_RESULT/'状态.json').read_text())['当前客机PID'] is None,'构建/功能阶段未收尾')
    functional=json.loads((PROJECT/'research/rrzc/results/benefit-device-20261019/最终功能核验.json').read_text())
    require(functional['两次功能核验通过'],'前置真实设备功能核验未通过')
    require(json.loads((PILOT/'greedy/独立核验.json').read_text())['验收通过'],'旧同负载先导未通过')
    require(FILES[2].read_bytes()==(PILOT/'greedy/20261017/workload.properties').read_bytes(),'负载与先导不同')
    props=dict(s.split('=',1) for s in FILES[2].read_text().splitlines() if '=' in s and not s.startswith('#'))
    require(independent_profile(props,1200000,'50/50',(4096,258048)),'不是已登记负载')
    require(sha(PROJECT/'build-femu/qemu-system-x86_64')==
        (BUILD_RESULT/'构建源码/FEMU.sha256').read_text().split()[0],'FEMU与设备功能验证不同')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8096))!=0,'8096端口被占用')
    for mode in MODES:
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
            Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    resources()


class BenefitPair(DeviceRun):
    label_prefix=LABEL
    parent_image=PARENT
    port='8096'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state.update(模式顺序=list(MODES),已完成=[],业务请求数=1200000,观察上限秒数=3000)

    def collect_target(self,out,stage):
        archive=f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/selftest-*/db" -czf {archive} -C {DIRECTORY} {NAME}',180)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(out)],180)
        self.command(['tar','-xzf',str(out/Path(archive).name),'-C',str(out)])

    def prepare_sources(self):
        plan=dict(目的='验证收益预算victim selection的闭环效果，不预设RR或总搬运改善',
            模式顺序=list(MODES),父镜像=str(PARENT),设备参数=DEVICE,
            唯一策略差异='Greedy为guarded=shadow且收益预算未配置；新组为guarded=execute及BENEFIT_BUDGET_MODE=execute',
            不变项='同静态库及二进制、8GiB几何、RR4096、原GC20%、192MiB回收资格、10秒界限、留存仅observe、公共分配与迁移管理；旧策略不改',
            负载='沿用1GiB范围YCSB-C++派生负载，非标准Workload A；655360个4KiB键、120万次请求、50%读50%更新、单线程、种子20261017、1000ops/s；无WAL/无块缓存',
            键范围='热读[0,4096)，相邻更新[4096,262144)，不保证物理共置',
            验证='逐组普通目录双次小回归；软件身份；全空模拟盘；实际请求轨迹；在线读值及全键最新值；独立逐轮预算/选择/源及尾部复核；完整维护账目',
            指标='业务期及全程RR/ZC、总迁移、读与Update均值/p95/p99、Run吞吐、实际改选及额外有效量、执行护栏拒绝原因',
            收尾='每组成功或异常均归档并正常关机，确认原PID退出后才开始下一组；归档失败也尝试正常关闭并明确标记证据不足',
            资源='最多一台4GiB客机加8GiB模拟NAND，启动保护余量16GiB内存及24GiB磁盘；不降低保护门槛',
            停止='每组最多3000秒；180秒无进展或后台错误则采证关机；首组未通过不启动第二组，不自动重跑',
            结论边界='单次配对不声称稳态或显著性；零改选不将波动归因于模型；相同请求不保证压实和物理布局相同')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        for name,paths in (('分析入口',(PROJECT/'research/rrzc').glob('*.py')),('宿主入口',TESTS.glob('*.py'))):
            target=source/name;target.mkdir();self.command(['cp',*map(str,paths),str(target)])
        self.command(['cp',str(BUILD_RESULT/'同库身份.json'),str(source/'构建同库身份.json')])
        (source/'FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        expected=json.loads((BUILD_RESULT/'同库身份.json').read_text())
        frozen=[source/p.name for p in FILES]
        manifest=''.join(f'{digest}  {name}\n' for name,digest in expected.items())+''.join(
            sha(p)+'  '+TARGET+'/'+p.name+'\n' for p in frozen)
        (source/'software.sha256').write_text(manifest)
        (OUTPUT/'同库身份.json').write_text(json.dumps(hashes(manifest),ensure_ascii=False,indent=2))
        return source,expected,frozen

    def observe_business(self,mode,out):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<3000:
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.record(阶段=mode+'：装载、120万次业务及独立全键校验',最近观测=point)
            with (out/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            require(not point['错误'],'检测到后台错误，归档并正常关机，不自动重跑')
            if self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成':return
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(time.monotonic()-changed<180,'连续180秒无进展，停止并采证')
            time.sleep(15)
        raise RuntimeError('超过本组3000秒上限，停止并采证')

    def capture_diagnostics(self,out):
        point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
        (out/'收尾现场.json').write_text(json.dumps(point,ensure_ascii=False,indent=2))
        log=point.get('维护日志路径')
        if log:
            require(Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),
                '不是已确认的唯一新维护日志')
            # 客机内包装器失败时也归档原/tmp日志，不把仅保存qcow2称为完整现场。
            self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(out/'现场维护.log')],180)

    def arm(self,mode,source,expected,files):
        resources()
        out=OUTPUT/mode;out.mkdir()
        self.record(当前模式=mode,已归档并正常关机=False)
        try:
            self.start(mode)
            query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
            identity=self.ssh(query);require(hashes(identity)==expected,'软件与已验证构建不同')
            (out/'软件.before.sha256').write_text(identity)
            self.ssh(f'test ! -e {TARGET} && test ! -e {RESULT} && mkdir {TARGET}')
            self.command(self.scp()+[*map(str,files),str(source/'software.sha256'),f'femu@127.0.0.1:{TARGET}/'])
            self.ssh(f'nohup setsid bash {TARGET}/check_ycsb_benefit_pair_guest.sh < /dev/null > {TARGET}/selftest-launcher.log 2>&1 &')
            self.record(阶段=mode+'：普通目录双次回归，不打开NVMe')
            self.wait_for(TARGET+'/selftest.finished',f'test ! -f {TARGET}/selftest.exit || cat {TARGET}/selftest.exit',300)
            require(self.ssh(f'cat {TARGET}/selftest.exit').strip()=='0','普通目录测试失败，不启动设备业务')
            for name in ('first','second'):
                require('键数=2560' in self.ssh(f'cat {TARGET}/selftest-{name}/verify.log'),'普通目录全键校验缺失')
            require(self.ssh(query)==identity,'小测试期间软件发生变化')
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_benefit_pair_guest.sh {mode} < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business(mode,out)
        except BaseException as error:
            (out/'运行异常.json').write_text(json.dumps(dict(类型=type(error).__name__,
                原因=str(error) if isinstance(error,(RuntimeError,ValueError)) else '认证或连接异常，仅记录类型，避免记录认证内容'),
                ensure_ascii=False,indent=2))
            raise
        finally:
            # 无论装载、测试、业务、归档在哪一步失败，均尝试精确正常关机。
            evidence_errors=[]
            try:self.capture_diagnostics(out)
            except Exception as error:
                evidence_errors.append(type(error).__name__)
                (out/'现场采证异常.json').write_text(json.dumps(dict(错误类型=evidence_errors),ensure_ascii=False,indent=2))
            finally:self.finish_stage(out,mode,RESULT)
        require(not evidence_errors,'现场日志采证未完成，不报告正式实验成功')
        require((out/NAME/'run.exit').read_text().strip()=='0','客机包装器未成功')
        root=out/'20261017'
        result=audit_run(root,mode)
        for name in ('first','second'):
            small=out/NAME/f'selftest-{name}'
            check(small/'audit.requests',2560,16,8192,update_range=(16,1008))
        check_options('\n'.join(p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布']=check(root/'audit.requests',655360,4096,1200000,update_range=(4096,258048))
        require(hashes((root/'software.sha256').read_text())==hashes((source/'software.sha256').read_text()),'归档软件清单与冻结输入不同')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'数据、决策或维护验收失败，停止后续组')
        self.state['已完成'].append(mode)
        self.record(阶段=mode+'完整核验通过，客机已关闭',已归档并正常关机=True,
            RR重置=result['RR重置'],ZC重置=result['ZC重置'],实际改选次数=result['实际改选次数'],吞吐量=result['吞吐量'])

    def run_pair(self):
        source,expected,files=self.prepare_sources()
        for mode in MODES:self.arm(mode,source,expected,files)
        result=compare(OUTPUT)
        (OUTPUT/'两组对照.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.password=None
        self.record(阶段='两组对照及完整验收完成，所有客机已关闭',当前客机PID=None,配对核验通过=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('前置功能、冻结库、同负载及资源检查通过，尚未启动');return
    password=getpass.getpass('隔离客机认证（仅内存）：');OUTPUT.mkdir()
    if args.detach:
        ready_read,ready_write=os.pipe();pid=os.fork()
        if pid:
            os.close(ready_write);require(os.read(ready_read,1)==b'1','监督分离未就绪');os.close(ready_read)
            print(f'监督PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.close(ready_read);os.setsid()
        log=os.open(OUTPUT/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(ready_write,b'1');os.close(ready_write)
    run=BenefitPair(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    try:run.run_pair()
    except BaseException as error:
        run.record(阶段='本轮停止，不自动重跑；归档及客机状态见记录',
            错误=str(error) if isinstance(error,(RuntimeError,ValueError)) else type(error).__name__)
        raise


if __name__=='__main__':main()
