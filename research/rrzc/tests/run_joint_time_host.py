#!/usr/bin/env python3
"""同窗成本核隔离构建及三类真实受控配对；不自动启动YCSB。"""
import argparse,getpass,json,signal
from pathlib import Path
import run_joint_host as base
from run_joint_host import PROJECT,IMAGE_DIR,require,digest_manifest
from run_strict_rr_build_host import StrictBuild
from run_window_credit_host import WindowRun,resources,digest
from analyze_joint_time import analyze_arm,compare

LABEL='joint-time-consistent-v1-20261023'
SOURCE=IMAGE_DIR/'joint-deadline-v2-20261022-results'
PARENT=IMAGE_DIR/'joint-deadline-v2-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
CASES=('opportunity','stopped','retire');MODES=('space','joint')
FILES=[TESTS/n for n in ('build_joint_time_guest.sh','run_joint_time_guest.sh','joint-time-client.patch',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','joint_read_trace.h','strict_stats_guest.py',
    'test_joint_time_selector.cc')]
FILES += [ROOT/'model/rrzc_joint_time_selector.h',ROOT/'zenfs-joint-time.patch']

class JointTimeRun(StrictBuild):
    collect=WindowRun.collect

    def __init__(self,args,*rest):
        self.extend_build_root=args.extend_build_root
        super().__init__(args,*rest)

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        for p in [*FILES,Path(__file__).resolve()]:(frozen/p.name).write_bytes(p.read_bytes())
        for directory,pattern,title in ((ROOT,'*.py','分析'),(ROOT/'model','*.h','模型'),(TESTS,'*.py','监督')):
            target=frozen/title;target.mkdir()
            for p in directory.glob(pattern):(target/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({str(p.relative_to(PROJECT)):digest(p)
            for p in [*FILES,Path(__file__).resolve()]},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            free=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            self.record(构建根盘可用字节=free)
            if free<6<<30:
                require(self.extend_build_root,'新子镜像根盘不足6GiB；未授权自动扩容，停止而不删文件')
                result=self.root('''set -euo pipefail
test "$(findmnt -n -o SOURCE,FSTYPE /)" = '/dev/mapper/ubuntu--vg-ubuntu--lv ext4'
vgs --units b --nosuffix -o vg_free --noheadings ubuntu-vg | awk '{if ($1<8589934592) exit 1}'
df -B1 /
lvextend -L +8G /dev/mapper/ubuntu--vg-ubuntu--lv
resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv
df -B1 /
''',120)
                (self.output/'新子镜像根盘扩容.log').write_text(result)
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'父镜像软件身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+
                [str(frozen/'parent.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_joint_time_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译同窗成本、新受控布局；不改旧版本及RR')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','构建失败，证据已保留且客机关闭')
        actual=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
        require(all(actual.get(n)==d for n,d in expected.items() if n not in changed),'旧模型、RR或旧程序改变')
        require(actual['plugin/zenfs/fs/rrzc_joint_time_selector.h']==digest(frozen/'rrzc_joint_time_selector.h'),'实际新核身份不符')
        (self.output/'同库身份.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='新成本核、整库及受控程序构建验收通过，客机已关闭')

    def arm(self,case,mode):
        require(case in CASES and mode in MODES,'未登记受控组')
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        tag=case+'-'+mode;out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+case
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,'运行前软件身份不符')
            self.root(f'nohup setsid bash {self.target}/run_joint_time_guest.sh {mode} {case} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实预读928及原生ZC/RR，非YCSB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',360)
        finally:self.finish(out,remote)
        result=analyze_arm(out/case,mode,case)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'载荷、评分及严格RR账目通过，客机已关闭',最近结果=result['窗口内'])
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL);p.add_argument('--cases',nargs='+',choices=CASES,default=list(CASES))
    p.add_argument('--extend-build-root',action='store_true',help='不足6GiB时只扩新构建子镜像根卷8GiB，不改SSD')
    a=p.parse_args();a.phase='build';a.scenarios=[]
    require(len(set(a.cases))==len(a.cases),'场景重复')
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(a);before=resources(profile)
    state=json.loads((SOURCE/'状态.json').read_text());require(state['当前客机PID'] is None,'来源实验尚未收尾')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU版本漂移')
    for case in a.cases:
        for mode in MODES:
            tag=a.label+'-'+case+'-'+mode
            for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
                require(not path.exists(),'拒绝覆盖目标：'+str(path))
    if a.check:print(json.dumps(dict(预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('同窗受控配对认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    plan=dict(目的='单独修正RR概率与有效量的同窗口径，真实验证联合机会和负例，不自动长测',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        场景=a.cases,模式=list(MODES),保持='4GiB/32区/128MiB区/128块、严格RR1024、GC20%及公共安全；读速率估计不改',
        模型='事件截止原窗口或已观察减少速率预计的有效量归零；同一事件条件时间估留存；泊松和线性留存未校准',
        布局='原22份填充中的最后一份只写64MiB，两组相同；空闲18%，让4/5MiB候选各自足以回收当前缺口',
        读取='真实预读928；随后持续16次每秒共128次，停读0次，退役64次后第4秒删除；不注入RC不强制选区',
        计量='释放后25秒窗口、全业务及独立校验分账；载荷、同块代际、概率及方案重算；不是YCSB性能结果',
        根盘='允许不足时仅新子镜像扩8GiB' if a.extend_build_root else '不足则停止，不自动扩容',
        收尾='逐组串行，成功失败均哈希归档后正常关闭，核验PID与SSH，不自动重跑',
        边界='预定未来退役仅是工作负载安排，不输入评分；不能期望模型知道未来删除；不预设所有场景正收益')
    (output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    run=JointTimeRun(a,password,output,profile);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build()
        for case in a.cases:
            arms={m:run.arm(case,m) for m in MODES};result=compare(arms)
            for m in MODES:
                archive=json.loads((output/(case+'-'+m)/'阶段归档.json').read_text())
                require(archive['完整'] and not archive['客机仍存活'],'归档或退出未通过')
            require((output/(case+'-space')/case/'software.before.log').read_bytes()==
                (output/(case+'-joint')/case/'software.before.log').read_bytes(),'两组软件库或程序不同')
            (output/(case+'配对核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            run.record(阶段=case+'配对验收完成，全部客机关闭',最近差值=result['窗口内差值'])
        run.record(阶段='所请求同窗受控配对完成，全部客机关闭；未运行新YCSB')
    except BaseException as error:run.record(阶段='同窗受控实验停止，保留原始错误，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
