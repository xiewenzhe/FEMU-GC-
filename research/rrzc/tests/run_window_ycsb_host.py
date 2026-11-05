#!/usr/bin/env python3
"""冻结已验收窗口模型，重链接YCSB并串行运行小测及40万请求配对，不自动长测。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require, PROJECT, IMAGE_DIR, digest_manifest
from run_window_credit_host import WindowRun, resources, digest
from run_sst_probe_host import CLIENT_MANIFEST, SstRun
from run_rr_fair_long_v2_host import CollectedRun
from analyze_window_credit import analyze_pair as controlled_pair
from analyze_window_ycsb import analyze_arm, compare_pair, sizes

LABEL='joint-window-ycsb-v1-20261021'
SOURCE=IMAGE_DIR/'joint-window-credit-v2-20261021-results'
PARENT=IMAGE_DIR/'joint-window-credit-v2-20261021-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
STAGES=('smoke','short')
FILES=[TESTS/n for n in ('build_sst_probe_guest.sh','build_window_ycsb_guest.sh',
    'run_sst_probe_guest.sh','run_window_ycsb_guest.sh','workload-sst-probe.properties',
    'workload-stable-unlimited.properties','sst_probe_trace.py','verify_ycsb.cc','sst_device_probe.cc',
    'capture_ycsb_guest.py','capture_window_ycsb_guest.py')]
FILES += [TESTS/'build_window_client_guest.sh',TESTS/'ycsb-phase-timing.patch',
          PROJECT/'research/rrzc/model/rrzc_client_phase.h']


def checked_source():
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None,'源短测客机尚未退出')
    for tag in ['build',*[f'{case}-{mode}' for case in base.CASES for mode in ('greedy','window')]]:
        require(tag in state['已完成'],'源短测未完成：'+tag)
        archive=json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'],'源短测归档/退出未通过：'+tag)
    expected=json.loads(MANIFEST.read_text())
    plan=json.loads((SOURCE/'预注册计划.json').read_text())
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==plan['FEMU二进制SHA256'],
        'FEMU二进制与已通过短对照的版本不同')
    require(json.loads((SOURCE/'平台配置.json').read_text())==base.controlled_profile(),
        '设备配置与已通过短对照的版本不同')
    require(digest(PROJECT/'research/rrzc/model/rrzc_window_credit_selector.h')==
        expected['plugin/zenfs/fs/rrzc_window_credit_selector.h'],'模型已漂移，不能称为冻结版本')
    return expected,{case:controlled_pair(SOURCE,case) for case in base.CASES}


class WindowYcsbRun(WindowRun):
    # 新实验可显式替换接线，默认仍严格保留原窗口实验语义。
    files=FILES
    manifest=MANIFEST
    modes=('greedy','window')
    capture_adapter='capture_window_ycsb_guest.py'
    build_script='build_window_ycsb_guest.sh'
    run_script='run_window_ycsb_guest.sh'
    title='窗口'
    extra_dependencies=()
    # 新实验必须显式声明可改的构建路径；旧入口仍要求原库逐字节不变。
    allowed_build_changes=()
    analyze=staticmethod(analyze_arm)

    def freeze_extra(self,frozen):
        pass
    # 复用已验收的监督：只认请求数进展，原30秒RR健康门槛不变；不回退旧监督。
    observe=CollectedRun.observe
    sample_threads=CollectedRun.sample_threads
    close_guest=CollectedRun.close_guest
    finish=CollectedRun.finish

    def __init__(self,*args):
        super().__init__(*args)
        self.control=self.target+'-evidence';self.deployed=False
        self.state.update(边界='4GiB真实不限速YCSB-C++，无WAL；非32GiB正式规模，不预设收益或稳态',
            模式顺序=[s+'-'+m for s in STAGES for m in self.modes],无进展上限秒=180,
            单臂上限秒=1500,采证目录=self.control)

    def start(self,mode):
        self.deployed=False
        # 业务子镜像继承构建目录；每阶段独立采证，既不覆盖也不删除父镜像证据。
        self.control=self.target+'-evidence-'+mode
        self.state['采证目录']=self.control
        super().start(mode)
        self.ssh(f'test ! -e {self.control} && mkdir {self.control}')
        # 别名适配只发生于本轮独立目录，原采证主体不作修改。
        for local,remote in (('capture_ycsb_guest.py','capture_ycsb_core.py'),
                             (self.capture_adapter,'capture_ycsb_guest.py')):
            source=self.output/'源码'/local
            self.command(self.scp()+[str(source),f'femu@127.0.0.1:{self.control}/{remote}'])
            require(self.ssh(f'sha256sum {self.control}/{remote}').split()[0]==digest(source),'部署采证脚本身份不符')
        self.ssh(f'python3 -m py_compile {self.control}/capture_ycsb_guest.py {self.control}/capture_ycsb_core.py')
        self.deployed=True

    def collect(self,out,remote):
        if remote!=self.target:return super().collect(out,remote)
        # 构建目录不复制旧对象和普通目录数据库；保留源包、链接/自测日志和程序身份。
        SstRun.collect(self,out,remote)
        archive=f'{self.target}-{self.state["当前阶段"]}-evidence.tar.gz'
        expected=self.ssh(f'sha256sum {archive}').split()[0]
        require(digest(out/Path(archive).name)==expected,'构建采证包传输哈希不符')

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        dependencies=[Path(__file__).resolve(),TESTS/'run_window_credit_host.py',TESTS/'run_joint_host.py',
            TESTS/'run_sst_probe_host.py',TESTS/'run_rr_fair_long_v2_host.py',
            TESTS/'run_rr_fair_long_host.py',TESTS/'run_rr_fair_ycsb_host.py',
            TESTS/'run_stable_ycsb_host.py',TESTS/'run_tail_ablation_smoke_pair.py',
            TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_stream_pair.py',*self.extra_dependencies]
        for p in self.files+dependencies:(frozen/p.name).write_bytes(p.read_bytes())
        analysis=frozen/'分析';analysis.mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(analysis/p.name).write_bytes(p.read_bytes())
        self.freeze_extra(frozen)
        (frozen/'身份.json').write_text(json.dumps({p.name:digest(p) for p in self.files+dependencies},ensure_ascii=False,indent=2))
        expected=json.loads(self.manifest.read_text());old=json.loads(CLIENT_MANIFEST.read_text())
        prefix='/home/femu/rrzc-ycsb-retention-20261017-tests/ycsb/'
        clients={n:d for n,d in old.items() if n.startswith(prefix)}
        require(len(clients)>=6,'缺原客户端源码身份');out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            for values,name in ((expected,'parent.software.sha256'),(clients,'client.parent.sha256')):
                raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(values),120)
                require(digest_manifest(raw)==values,'客机父库或旧客户端身份不符')
                (frozen/name).write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in self.files]+[
                str(frozen/'parent.software.sha256'),str(frozen/'client.parent.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/{self.build_script} < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='新YCSB重链接冻结'+self.title+'库，四次普通目录自测；不重新调模型')
            self.wait_for(self.target+'/window.build.finished',f'test ! -f {self.target}/ycsb-build.log || tail -1 {self.target}/ycsb-build.log',900)
            self.ssh(f'tar --exclude="*.o" -czf {self.target}/client-source.tar.gz -C {self.target}/ycsb Makefile core rocksdb rrzc_ycsb_audit.h',60)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require(all((built/n).read_text().strip()=='0' for n in ('window.build.exit','build.exit','selftest.exit')),
            '真实重链接或普通目录自测失败，停止业务')
        software=digest_manifest((built/'software.sha256').read_text())
        require(all(software.get(n)==v for n,v in expected.items() if n not in self.allowed_build_changes),
            '未授权改变的原模型、RR路径或旧客户端被改变')
        require(all(software.get(n)!=expected.get(n) for n in self.allowed_build_changes),
            '声明的新接线未确认重新编译')
        require(software[self.target+'/ycsb/ycsb']!=clients[prefix+'ycsb'],'新YCSB仍是原二进制')
        require('librocksdb_debug.a' in (built/'ycsb-build.log').read_text(),'未记录真实链接命令')
        require('新开关及UTF-8日志字节存在' in (built/'窗口链接核验.log').read_text(),'新模型链接分支不完整')
        for mode in ('disjoint','overlap'):
            a,b=[built/f'selftest-{mode}-{n}' for n in ('first','second')]
            require((a/'audit.requests').read_bytes()==(b/'audit.requests').read_bytes() and
                (a/'audit.expected').read_bytes()==(b/'audit.expected').read_bytes(),'普通目录请求/最终值不一致')
            for p in (a,b):require('键数=1024' in (p/'verify.log').read_text(),'普通目录最新值校验未通过')
            from client_phase import phases
            for p in (a,b):require(phases((p/'ycsb.log').read_text(),8192)['可知'],'客户端阶段计时自测缺失')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段=self.title+'YCSB重链接及四次自测通过，构建客机已归档关闭')

    def arm(self,stage,mode):
        require(stage in STAGES and mode in self.modes,'未登记业务')
        tag=stage+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-sst-results/'+tag
        expected=json.loads((self.output/'同库身份.json').read_text());error=None
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,
                '运行前冻结模型或新客户端身份不符')
            self.root(f'nohup setsid bash {self.target}/{self.run_script} {tag} {mode} {sizes(stage)[1]} '
                f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：不限速真实YCSB及全键校验',客机结果目录=remote,计划业务请求数=sizes(stage)[1])
            self.observe(tag)
        except BaseException as cause:
            error=cause;self.record(原始业务或监督错误=type(cause).__name__+'：'+str(cause));raise
        finally:
            try:self.finish(out,remote)
            except Exception as cleanup:
                if error is None:raise
                self.record(收尾另有错误=str(cleanup))
        result=self.analyze(out/tag,stage,mode)
        require(result['客户端阶段耗时']['可知'],'新计时配对缺少完整客户端阶段时间，不能作为已验收结果')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'数据/实际评分/维护核验通过，已归档关闭',最近结果={n:result[n]
            for n in ('RR次数','ZC次数','总迁移字节','总成功ZoneReset','吞吐量','客户端阶段耗时')})


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile);expected,source=checked_source()
    require(CLIENT_MANIFEST.is_file(),'缺客户端来源清单')
    for s in STAGES:
        for m in ('greedy','window'):
            for p in (IMAGE_DIR/f'{args.label}-{s}-{m}.qcow2',Path(f'/tmp/{args.label}-{s}-{m}.pid'),
                Path(f'/tmp/{args.label}-{s}-{m}.qmp'),Path(f'/tmp/{args.label}-{s}-{m}-ssh.sock')):
                require(not p.exists(),'新目标已存在：'+str(p))
    if args.check:print(json.dumps(dict(只读预检通过=True,资源=before,源短对照均重新核验通过=True),ensure_ascii=False));return
    password=getpass.getpass('窗口YCSB隔离客机认证（仅驻留内存）：')
    output.mkdir();qemu_digest=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'源短对照复核.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='冻结已通过四类真实短对照的窗口模型，检验真实YCSB总维护收益，不继续调参',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=qemu_digest,
        阶段=['build','smoke-greedy','smoke-window','short-greedy','short-window'],
        负载='小测8192键/8192请求，短对照491520键/400000请求；同种子20261017、单线程、读更新各50%、4KiB字段',
        键范围='短对照读[0,4096)，更新[0,65536)；逻辑重叠不保证物理同区',
        不变='4GiB/128MiB区/128块每区/RR1024/GC20%/原资格及安全路径，两臂RR公平队列均开启',
        限速='limit.ops=0，无limit.file，完全不限速；无WAL、无块缓存',
        指标='总迁移/成功ZoneReset及推导块擦除、RR/ZC、API平均及P99/P99.9/P99.99、原始Run吞吐及分离前台请求/Cleanup时间',
        计时变更='仅新客户端在单线程请求循环及原Cleanup前后记录四个单调时钟；两臂统一开启，不改变请求、原Run计时、库或模型',
        监督='单臂1500秒，业务请求180秒无进展即采证关机；双方相同采证，成功失败均正常收尾',
        收尾='串行一台；进行中及退出前另存只读证据，归档哈希复核、正常关机、PID与SSH退出，不盲目重跑',
        边界='API非裸设备延迟，UPDATE含Get再Put；不是32GiB正式规模、不预设稳态；本入口不自动长测',
        初始资源=before),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','监督分离失败');os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}',flush=True);return
        os.close(r);os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(w,b'1');os.close(w)
    signal.signal(signal.SIGTERM,interrupted)
    run=WindowYcsbRun(args,password,output,profile);run.qemu_digest=qemu_digest
    try:
        run.build()
        for stage in STAGES:
            for mode in ('greedy','window'):run.arm(stage,mode)
            result=compare_pair(output,stage)
            with (output/(stage+'配对核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=stage+'同请求、同库及数据/维护配对通过，两臂已关闭',最近差值=result['模型相对Greedy差值'])
        run.record(阶段='新模型YCSB小测与40万请求配对完成，全部客机关闭，未启动长测')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
