#!/usr/bin/env python3
"""隔离编译边界核，串行实测热文件提前退役负例；不自动进入YCSB长测。"""
import argparse,getpass,json,shutil,signal
from pathlib import Path
import run_joint_host as base
from run_joint_host import PROJECT,IMAGE_DIR,require,digest_manifest
from run_joint_time_host import JointTimeRun
from run_window_credit_host import resources,digest
from analyze_joint_time import analyze_arm

LABEL='joint-terminal-v1-20261023'
SOURCE=IMAGE_DIR/'joint-time-consistent-v1-20261023-results'
PARENT=IMAGE_DIR/'joint-time-consistent-v1-20261023-build.qcow2'
MANIFEST=SOURCE/'同库身份.json';ARCHIVE=SOURCE/'build/joint-time-consistent-v1-20261023'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[TESTS/n for n in ('build_terminal_guest.sh','run_terminal_guest.sh','test_terminal_selector.cc')]
FILES += [ROOT/'model/rrzc_terminal_selector.h',ROOT/'zenfs-terminal.patch']
PARENT_FILES=[ARCHIVE/n for n in ('joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','joint_read_trace.h','strict_stats_guest.py')]

class TerminalRun(JointTimeRun):
    files=FILES
    parent_files=PARENT_FILES
    manifest=MANIFEST
    build_script='build_terminal_guest.sh'
    def build(self):
        frozen=self.output/'源码';frozen.mkdir();sources=[*self.files,*self.parent_files,Path(__file__).resolve()]
        for p in sources:shutil.copy2(p,frozen/p.name)
        for directory,pattern,title in ((ROOT,'*.py','分析'),(ROOT/'model','*.h','模型'),(TESTS,'*.py','监督')):
            target=frozen/title;target.mkdir()
            for p in directory.glob(pattern):shutil.copy2(p,target/p.name)
        (frozen/'身份.json').write_text(json.dumps({str(p):digest(p) for p in sources},ensure_ascii=False,indent=2))
        expected=json.loads(self.manifest.read_text());out=self.output/'build';out.mkdir()
        for p in self.parent_files:
            require(digest(p)==expected['/home/femu/joint-time-consistent-v1-20261023/'+p.name],'真实父客户端身份改变')
        try:
            self.start('build');self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            free=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            require(free>=6<<30,'新子镜像根盘不足6GiB，不删除旧文件或自动扩容')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'父镜像实际身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.command(self.scp()+[str(frozen/p.name) for p in [*self.files,*self.parent_files]]+
                [str(frozen/'parent.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/{self.build_script} < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译默认关闭的边界核；旧核、RR及真实负载不改')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','客机构建失败，日志已保留且关机')
        actual=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
        require(all(actual.get(n)==d for n,d in expected.items() if n not in changed),'旧模型、RR或旧程序改变')
        require(actual['plugin/zenfs/fs/rrzc_terminal_selector.h']==digest(frozen/'rrzc_terminal_selector.h'),'新核部署身份不符')
        (self.output/'同库身份.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='边界核单元、整库及真实客户端构建通过，客机关闭')

    def arm(self,mode):
        tag='retire-'+mode;out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/retire'
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,'运行前实际库或程序不符')
            self.root(f'nohup setsid bash {self.target}/run_terminal_guest.sh {mode} retire < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实相同初态与64次读取，第4秒预定退役不输入模型',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',360)
        finally:self.finish(out,remote)
        result=analyze_arm(out/'retire','joint','retire',terminal=mode=='terminal')
        raw=(out/'retire/zenfs.run.log').read_text()
        require(raw.count('边界配置：版本=1，启用='+str(int(mode=='terminal'))+'，')==1,'实际策略开关不符')
        result['实际模型']=mode
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'载荷、实际评分及维护分账通过，客机关闭',最近结果=result['全业务'])
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--label',default=LABEL)
    a=p.parse_args();a.phase='build';a.scenarios=[];a.extend_build_root=False
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(a);before=resources(profile)
    require(json.loads((SOURCE/'状态.json').read_text())['当前客机PID'] is None,'父实验尚未收尾')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU版本漂移')
    for mode in ('old','terminal'):
        tag=a.label+'-retire-'+mode
        for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
            require(not path.exists(),'拒绝覆盖：'+str(path))
    if a.check:print(json.dumps(dict(只读预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('边界成本受控配对认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(目的='先检验空间边界估价能否减少旧模型的提前复制回归，不预设优于Greedy',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        不变='4GiB/32区/128MiB区/128块、原GC20%、严格RR1024、同块读预测及原留存、同负载真实重建',
        新核='以原候选方案的最大同窗预计空间为共同边界，互斥回收不重复；未清非RR分支以同窗留存作分数复制估价',
        边界='不是未来GC次数预测或严格数学下界；未来退役安排不输入模型，空间预测不用于安全准入；未知保留旧选择',
        对照='同一新库，ZENFS_RRZC_TERMINAL=0/1；两组均RR成本开启，不是stock或空间Greedy',
        负载='复用上一轮实际客户端，冷4MiB热8MiB，真实RC928，64次16/s后第4秒退役，观察25秒',
        验收='整库与客户端编译、UBSan核、独立数值重算、候选载荷及原维护账目、终态硬件空间；无新YCSB性能结论',
        收尾='单台串行，成功失败均完整哈希归档后正常关机、核验PID与SSH，不自动重跑'),ensure_ascii=False,indent=2))
    run=TerminalRun(a,password,output,profile);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build();arms={mode:run.arm(mode) for mode in ('old','terminal')}
        require(all(arms['old'][k]==arms['terminal'][k] for k in ('初态','请求计划')),'真实初态或请求安排不同')
        require((output/'retire-old/retire/software.before.log').read_bytes()==
                (output/'retire-terminal/retire/software.before.log').read_bytes(),'两组实际软件不同')
        result=dict(验收通过=True,模式=arms,全业务差值={k:arms['terminal']['全业务'][k]-arms['old']['全业务'][k]
            for k in arms['old']['全业务']},边界='旧模型与边界新核的单次受控负例，不证明优于空间Greedy或YCSB稳态收益')
        (output/'配对核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        run.record(阶段='边界成本负例配对完成并独立分析，全部客机关闭',最近差值=result['全业务差值'])
        print(json.dumps(dict(完成=True,结果=str(output/'配对核验.json'),差值=result['全业务差值']),ensure_ascii=False))
    except BaseException as error:run.record(阶段='边界成本实验停止，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
