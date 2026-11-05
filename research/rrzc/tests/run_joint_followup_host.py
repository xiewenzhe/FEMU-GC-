#!/usr/bin/env python3
"""不改库和模型，串行验证相同后续128MiB真实写入是否补回冷区GC复制。"""
import argparse,getpass,json,signal
from pathlib import Path
import run_joint_host as base
from run_joint_host import PROJECT,IMAGE_DIR,require,digest_manifest
from run_joint_time_host import JointTimeRun
from run_window_credit_host import resources,digest
from analyze_joint_followup import analyze_arm,compare

LABEL='joint-followup-space-v1-20261023'
SOURCE=IMAGE_DIR/'joint-time-consistent-v1-20261023-results'
PARENT=IMAGE_DIR/'joint-time-consistent-v1-20261023-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ARCHIVE=SOURCE/'build/joint-time-consistent-v1-20261023'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
NEW=('build_joint_followup_guest.sh','joint_followup.h','joint-followup-client.patch')
OLD=('run_joint_time_guest.sh','joint_opportunity_smoke.cc','v1_execution_smoke_test.cc',
     'joint_read_trace.h','strict_stats_guest.py')

class FollowupRun(JointTimeRun):
    def build(self):
        import shutil
        frozen=self.output/'源码';frozen.mkdir()
        files=[TESTS/n for n in NEW]+[ARCHIVE/n for n in OLD]
        for p in files+[Path(__file__).resolve()]:shutil.copy2(p,frozen/p.name)
        for directory,pattern,title in ((ROOT,'*.py','分析'),(ROOT/'model','*.h','模型'),(TESTS,'*.py','监督')):
            target=frozen/title;target.mkdir()
            for p in directory.glob(pattern):shutil.copy2(p,target/p.name)
        (frozen/'身份.json').write_text(json.dumps({str(p):digest(p) for p in files},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        # 使用上一轮实际编译过的客户端，而不是未打补丁的仓库模板。
        for name in OLD:
            require(digest(frozen/name)==expected['/home/femu/joint-time-consistent-v1-20261023/'+name],
                    '归档父程序身份漂移：'+name)
        try:
            self.start('build')
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            free=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            require(free>=1<<30,'根盘不足1GiB，不删除旧文件或扩盘')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'父镜像实际软件身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.command(self.scp()+[str(frozen/p.name) for p in files]+
                [str(frozen/'parent.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_joint_followup_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='仅编译后续压力客户端；所有旧源码、库与模型不变')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/program-build.log || tail -1 {self.target}/program-build.log',180)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','客户端构建失败，客机已采证关闭')
        actual=digest_manifest((built/'software.sha256').read_text())
        require(all(actual.get(n)==d for n,d in expected.items()),'库、模型或旧程序意外变化')
        (self.output/'同库身份.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='独立客户端编译通过，库与模型身份不变；客机关闭')

    def arm(self,mode):
        tag='opportunity-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/opportunity'
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,
                    '运行前实际库或程序身份不符')
            self.root(f'nohup setsid bash {self.target}/run_joint_time_guest.sh {mode} opportunity < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：重建真实初态，25秒后追加同样128MiB并等待25秒',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',360)
        finally:self.finish(out,remote)
        result=analyze_arm(out/'opportunity',mode)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'真实载荷及分阶段账目通过，客机关闭',最近结果=result['全业务'])
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL);a=p.parse_args();a.phase='build';a.scenarios=[];a.extend_build_root=False
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    out,profile=base.preflight(a);before=resources(profile)
    require(json.loads((SOURCE/'状态.json').read_text())['当前客机PID'] is None,'父实验尚未收尾')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU版本变化')
    for mode in ('space','joint'):
        tag=a.label+'-opportunity-'+mode
        for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
            require(not path.exists(),'拒绝覆盖：'+str(path))
    if a.check:print(json.dumps(dict(只读预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('后续空间压力配对认证（仅内存）：');out.mkdir()
    (out/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (out/'预注册计划.json').write_text(json.dumps(dict(目的='检验初窗少搬4MiB是否只是延后冷区GC；不预设正收益',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        不变='已编译库、所有模型、严格RR1024、GC20%、4GiB/32区、空间对照与模型开关均沿用上一轮',
        负载='逐组新NAND，真实相同初态和128次热读；观察25秒后再同写128MiB有效文件，完成后统一等25秒',
        保留='冷热候选全程存活；后续写入不向模型透露；不修改回收门槛、不直接调用GC或RR',
        验收='编译、实际软件身份、候选及新增文件全量校验、填充首尾抽检、维护分阶段分账、最终实际硬件可写空间',
        收尾='串行逐组归档并核验哈希后正常关机，失败也收尾；不自动重试，不是YCSB或论文稳态实验'),ensure_ascii=False,indent=2))
    run=FollowupRun(a,password,out,profile);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build();arms={mode:run.arm(mode) for mode in ('space','joint')}
        require((out/'opportunity-space/opportunity/software.before.log').read_bytes()==
                (out/'opportunity-joint/opportunity/software.before.log').read_bytes(),'两组库或程序不同')
        result=compare(arms)
        (out/'配对核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        run.record(阶段='后续同量写入配对完成并独立核验，全部客机关闭',最近差值=result['全业务差值'])
        print(json.dumps(dict(完成=True,结果=str(out/'配对核验.json'),差值=result['全业务差值']),ensure_ascii=False))
    except BaseException as error:run.record(阶段='后续压力测试停止，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
