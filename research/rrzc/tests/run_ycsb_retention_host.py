#!/usr/bin/env python3
"""单次构建后四个新子镜像串行YCSB-C++对照；密码只驻留内存，失败保留现场。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_tail_ablation_fixed_pair import FixedPair, running_parent
from run_stream_pair import PROJECT, IMAGE_DIR, require
from run_rocksdb_guard_pair_host import hashes

LABEL='ycsb-retention-20261017'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PARENT=IMAGE_DIR/'rocksdb-guard-pair-20261016-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
UPSTREAM=PROJECT/'build-femu/rrzc/ycsb-upstream-20261017'
PORT='8093'
MODES=('greedy','v1','cost','retention')
FILES=[PROJECT/'research/rrzc'/n for n in ('zenfs-migration-window.patch','zenfs-retention-selector.patch')]
FILES += [PROJECT/'research/rrzc/model'/n for n in ('rrzc_migration_window.h','rrzc_retention_selector.h')]
FILES += [PROJECT/'research/rrzc/tests'/n for n in ('build_ycsb_retention_guest.sh','run_ycsb_retention_guest.sh',
    'workloada-zns.properties','rrzc_ycsb_audit.h','verify_ycsb.cc','test_retention_selector.cc','test_migration_window.cc','ycsb-reproducible.patch')]

def preflight(phase):
    require(Path('/dev/kvm').exists(),'宿主KVM不可用')
    parent=PARENT if phase=='build' else BUILD_IMAGE
    require(parent.is_file() and parent.resolve().parent==IMAGE_DIR and not running_parent(parent),'父镜像不存在或仍在运行')
    with socket.socket() as probe: require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8093已占用，保留已有客机')
    require((not OUTPUT.exists()) if phase=='build' else (OUTPUT/'同库身份.json').is_file(),'阶段输出状态不符')
    for mode in (('build',) if phase=='build' else MODES):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'新目标已存在，拒绝覆盖：'+str(p))
    for p,minimum in ((IMAGE_DIR,16<<30),(PROJECT,3<<30)):
        fs=os.statvfs(p); require(fs.f_bavail*fs.f_frsize>=minimum,'镜像或结果盘余量不足')

class YcsbRun(FixedPair):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state.pop('实验空间权重百分比',None)
        self.state['模式顺序']=['build',*MODES]
    def scp(self): return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
    def wait_for(self,marker,progress,limit):
        start=time.monotonic()
        while time.monotonic()-start<limit:
            message=self.ssh(f'if test -f {marker}; then printf "包装已结束\\n"; fi; {progress}; true')
            self.record(最近观测=message)
            if '包装已结束' in message: return
            time.sleep(15)
        raise RuntimeError('观察超时，保留客机和进程，不自动终止或重跑')
    def collect(self,output,remote):
        self.command(self.scp()+['-r',f'femu@127.0.0.1:{remote}',str(output)],300)
    def build(self):
        plan=dict(实现='上游YCSB-C++，不是官方Java版或自编业务负载',
            上游='https://github.com/ls4154/YCSB-cpp',版本='6c94fdb52350de66321ef5a8b529cf1b685e2338',
            直方图版本='733b470eced028fac82a3cbfd064f2dcf545c6bc',
            模式顺序=list(MODES),工作负载='A：50%读、50%更新，原Zipfian生成器，单字段4096字节',
            初始键数=655360,业务操作数=2400000,速率上限=4000,线程数=1,随机种子=20261017,
            设备GiB=8,RR阈值=4096,GC启动比例=20,GC斜率=3,回收资格MiB=192,额外迁移预算MiB=16,
            不变项='RR触发及刷新、GC触发、候选资格、分配及预留管理、旧50/50和80/20不变',
            新因素='连续满区同代际、无迁移的10/30秒有效量退役趋势，较低速率线性外推下次10秒检查时有效量',
            新排序='预计保留有效量减相对Greedy额外迁移量，正值才改选，保留原紧迫度及尾部源复核',
            假设边界='线性留存是假设，不是真实寿命；未知回退Greedy，不使用未来删除信息',
            对照='Greedy、原ETA优先V1、只按回收成本排序、新留存净收益；四组都启用同样采样及请求审计',
            指标='业务期及装载期维护量分开；RR/ZC尝试与Reset、总搬运、精确调用均值和p95/p99、实际改选、已知历史覆盖',
            正确性='软件哈希、实际请求轨迹SHA256配对、读值在线哈希检查、结束后独立只读全键最新值哈希校验',
            延迟口径='上游完整绑定调用；Update含Get再Put；审计在单次调用计时外，但影响墙钟吞吐',
            验收='不要求正收益；维护异常、请求不一致或数据错误均不得报成功；次数不足不称稳态',自动重跑=False)
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        frozen=OUTPUT/'实验源'; frozen.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(frozen)])
        archive=OUTPUT/'ycsb-source.tar.gz'
        self.command(['tar','--exclude=.git','--exclude=*.o','--exclude=*.d','-czf',str(archive),'-C',str(UPSTREAM),'.'])
        self.record(阶段='新模型和公开YCSB-C++四组对照已登记',固定计划=plan)
        self.start('build')
        expected=json.loads((PROJECT/'research/rrzc/results/rocksdb-guard-pair-20261016/同库身份.json').read_text())
        core={n:d for n,d in expected.items() if not n.startswith('/')}
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(core))
        require(hashes(before)==core,'停止父镜像核心身份不符')
        base=OUTPUT/'source.base.sha256'; base.write_text(before)
        self.ssh(f'test ! -e {DIRECTORY} && mkdir {DIRECTORY}')
        self.command(self.scp()+[*map(str,FILES),str(base),str(archive),f'femu@127.0.0.1:{DIRECTORY}/'],180)
        build=OUTPUT/'build';build.mkdir()
        try:
            self.ssh(f'nohup setsid bash {DIRECTORY}/build_ycsb_retention_guest.sh < /dev/null > {DIRECTORY}/build-launcher.log 2>&1 &')
            self.record(阶段='新子镜像完整编译库、YCSB及只读校验器，随后普通目录双次回归')
            self.wait_for(f'{DIRECTORY}/build.finished',f'test ! -f {DIRECTORY}/ycsb-build.log || tail -1 {DIRECTORY}/ycsb-build.log; test ! -f {DIRECTORY}/library-build.log || tail -1 {DIRECTORY}/library-build.log',1800)
        finally: self.collect(build,DIRECTORY)
        root=build/Path(DIRECTORY).name
        require((root/'build.exit').read_text().strip()=='0' and (root/'selftest.exit').read_text().strip()=='0','构建或最小回归失败，保留现场')
        software=hashes((root/'software.sha256').read_text())
        require(software['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256((PROJECT/'build-femu/rrzc/retention-work/fs/fs_zenfs.cc').read_bytes()).hexdigest(),'客机模型接线与冻结副本不同')
        changed=sorted(n for n in core if core[n]!=software.get(n))
        require(changed==['librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/fs_zenfs.h'],'核心修改超出已声明范围')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        (OUTPUT/'源包.sha256').write_text(self.command(['sha256sum',str(archive),'build-femu/qemu-system-x86_64']))
        self.record(阶段='构建及双次普通目录回归通过，正常关闭新构建客机')
        self.shutdown();self.record(当前客机PID=None,阶段='固定同库构建已完成，可启动四组YCSB对照')
    def repair(self):
        # 仅修正尚未运行设备业务的本轮构建客机；保留首次错误和全部旧归档。
        self.state=json.loads((OUTPUT/'状态.json').read_text())
        require(self.state['当前镜像']==str(BUILD_IMAGE),'修正对象不是本轮构建镜像')
        require(f'file={BUILD_IMAGE},'.encode() in Path(f"/proc/{self.state['当前客机PID']}/cmdline").read_bytes(),'客机PID或镜像不符')
        self.socket=f'/tmp/{LABEL}-build-ssh.sock';self.qmp=Path(f'/tmp/{LABEL}-build.qmp')
        old=self.ssh('cd /home/femu/rocksdb; sha256sum plugin/zenfs/fs/fs_zenfs.cc')
        require(old.split()[0]=='652ab39d4e3b5923a61f26a2fb953a10a187e73dba34c8bb9d3ee51d932b3684','待修正源身份不符')
        frozen=OUTPUT/'编译修正';frozen.mkdir()
        names=['build_ycsb_retention_guest.sh','zenfs-retention-release.patch']
        sources=[PROJECT/'research/rrzc/tests'/n for n in names]
        self.command(['cp',*map(str,sources),str(frozen)])
        self.command(self.scp()+[str(sources[1]),f'femu@127.0.0.1:{DIRECTORY}/'])
        self.ssh(f'set -e; test ! -e {DIRECTORY}/首次编译失败; mkdir {DIRECTORY}/首次编译失败; '
            f'cp {DIRECTORY}/build.exit {DIRECTORY}/library-build.log {DIRECTORY}/build-launcher.log '
            f'{DIRECTORY}/build_ycsb_retention_guest.sh {DIRECTORY}/首次编译失败/; '
            f'mv {DIRECTORY}/build.finished {DIRECTORY}/首次编译失败/; '
            f'cd /home/femu/rocksdb/plugin/zenfs; git apply --check {DIRECTORY}/zenfs-retention-release.patch; '
            f'git apply {DIRECTORY}/zenfs-retention-release.patch')
        self.command(self.scp()+[str(sources[0]),f'femu@127.0.0.1:{DIRECTORY}/'])
        self.ssh(f'nohup setsid bash {DIRECTORY}/build_ycsb_retention_guest.sh resume < /dev/null > {DIRECTORY}/build-launcher.log 2>&1 &')
        self.record(阶段='仅修正跨文件内联调用，重新编译并验证；未运行设备业务')
        try:self.wait_for(f'{DIRECTORY}/build.finished',f'test ! -f {DIRECTORY}/ycsb-build.log || tail -2 {DIRECTORY}/ycsb-build.log; tail -1 {DIRECTORY}/library-build.log',1800)
        finally:self.collect(frozen,DIRECTORY)
        root=frozen/Path(DIRECTORY).name
        require((root/'build.exit').read_text().strip()=='0' and (root/'selftest.exit').read_text().strip()=='0','修正后编译或回归仍失败，保留证据')
        software=hashes((root/'software.sha256').read_text())
        require(software['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256((PROJECT/'build-femu/rrzc/retention-work/fs/fs_zenfs.cc').read_bytes()).hexdigest(),'实际接线不符')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown(); self.record(当前客机PID=None,阶段='修正后构建和双次普通目录回归通过，可运行四组对照')
    def run_all(self):
        analysis=OUTPUT/'本轮分析源';analysis.mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(analysis)])
        self.command(['cp',str(Path(__file__).resolve()),str(analysis)])
        expected=json.loads((OUTPUT/'同库身份.json').read_text())
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        self.parent_image=BUILD_IMAGE
        for mode in MODES:
            output=OUTPUT/mode;require(not output.exists(),'结果已存在，拒绝覆盖');output.mkdir()
            self.start(mode)
            identity=self.ssh(query); require(hashes(identity)==expected,'实验软件不同')
            (output/'软件.before.sha256').write_text(identity)
            try:
                self.root(f'nohup setsid bash {DIRECTORY}/run_ycsb_retention_guest.sh {mode} < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
                self.record(阶段=f'{mode}：真实YCSB-C++装载及240万次业务运行')
                self.wait_for(f'{DIRECTORY}/run.finished',f'test ! -f {RESULT}/ycsb.log || tail -2 {RESULT}/ycsb.log',2400)
            finally:
                self.collect(output,RESULT)
                (output/'软件.after.sha256').write_text(self.ssh(query))
                (output/'包装器.log').write_text(self.ssh(f'test ! -f {DIRECTORY}/launcher.log || cat {DIRECTORY}/launcher.log'))
            require(identity==(output/'软件.after.sha256').read_text(),'运行中软件身份改变')
            from analyze_ycsb_retention import audit
            result=audit(output/'20261017',mode)
            (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            require(result['验收通过'],'业务、校验或维护异常；保留客机及原始结果')
            self.shutdown(); self.state['已完成'].append(mode)
            self.record(阶段=f'{mode}已归档核验并正常关机',当前客机PID=None)
        from analyze_ycsb_retention import compare
        result=compare(OUTPUT)
        (OUTPUT/'四组对照.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='四组真实YCSB-C++完成，结果已归档；收益以对照核验为准')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('build','repair','run'),required=True)
    parser.add_argument('--check',action='store_true'); args=parser.parse_args()
    if args.phase!='repair':preflight(args.phase)
    if args.check: print('只读前置检查通过，未启动客机或实验');return
    password=None if args.phase=='repair' else getpass.getpass('本轮隔离客机认证（只驻留内存）：')
    if args.phase=='build': OUTPUT.mkdir()
    run=YcsbRun(SimpleNamespace(label=LABEL,seconds=600,space_weight=50),password,OUTPUT)
    try:
        if args.phase=='repair':run.repair()
        elif args.phase=='build':run.build()
        else:run.run_all()
    except BaseException as error:
        run.record(阶段='停止并保留本轮客机及证据，不自动重跑',错误=str(error) or type(error).__name__)
        raise

if __name__=='__main__':main()
