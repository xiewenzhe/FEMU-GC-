#!/usr/bin/env python3
"""新模型不限速YCSB配对：重链接、先导、短对照、较长对照，串行归档关机。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require, PROJECT, IMAGE_DIR, digest_manifest
from run_sst_probe_host import SstRun, CLIENT_MANIFEST

LABEL='joint-stable-ycsb-v1-20261021'
PARENT=IMAGE_DIR/'joint-stable-credit-v1-20261021-build.qcow2'
MANIFEST=IMAGE_DIR/'joint-stable-credit-v1-20261021-results/同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_sst_probe_guest.sh','run_sst_probe_guest.sh','workload-sst-probe.properties',
    'build_stable_ycsb_guest.sh','run_stable_ycsb_guest.sh','workload-stable-unlimited.properties',
    'sst_probe_trace.py','verify_ycsb.cc','sst_device_probe.cc')]
STAGES=('smoke','short','long')


class StableYcsbRun(SstRun):
    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        dependencies=[Path(__file__).resolve(),Path(base.__file__).resolve(),TESTS/'run_sst_probe_host.py',
                      TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_stream_pair.py']
        for p in FILES+dependencies:(frozen/p.name).write_bytes(p.read_bytes())
        (frozen/'分析').mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(frozen/'分析'/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FILES+dependencies},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());old=json.loads(CLIENT_MANIFEST.read_text())
        prefix='/home/femu/rrzc-ycsb-retention-20261017-tests/ycsb/'
        clients={n:d for n,d in old.items() if n.startswith(prefix)}
        require(len(clients)>=6,'旧客户端身份不足');out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            for values,name in ((expected,'parent.software.sha256'),(clients,'client.parent.sha256')):
                raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(values))
                require(digest_manifest(raw)==values,'停止父镜像的软件身份不符：'+name)
                (frozen/name).write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            payload=[frozen/p.name for p in FILES]+[frozen/'parent.software.sha256',frozen/'client.parent.sha256']
            self.command(self.scp()+list(map(str,payload))+[f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_stable_ycsb_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='重链接新模型库的YCSB，普通目录四次自测及独立全键校验')
            self.wait_for(self.target+'/stable.build.finished',f'test ! -f {self.target}/ycsb-build.log || tail -1 {self.target}/ycsb-build.log',900)
            self.ssh(f'tar --exclude="*.o" -czf {self.target}/client-source.tar.gz -C {self.target}/ycsb Makefile core rocksdb rrzc_ycsb_audit.h',60)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require(all((built/n).read_text().strip()=='0' for n in ('stable.build.exit','build.exit','selftest.exit')),'构建或自测失败，不启动业务')
        software=digest_manifest((built/'software.sha256').read_text())
        require(all(software.get(n)==d for n,d in expected.items()),'不应修改新模型核心库')
        require(software[self.target+'/ycsb/ycsb']!=clients[prefix+'ycsb'],'未确认新的重链接二进制')
        require('librocksdb_debug.a' in (built/'ycsb-build.log').read_text(),'缺真实链接命令')
        for symbol in ('ZENFS_RRZC_STABLE_CREDIT','ZENFS_RRZC_PAIR_GREEDY'):
            require(symbol in (built/(symbol+'.log')).read_text(),'新YCSB缺模型分支：'+symbol)
        for mode in ('disjoint','overlap'):
            a,b=[built/f'selftest-{mode}-{repeat}' for repeat in ('first','second')]
            require((a/'audit.requests').read_bytes()==(b/'audit.requests').read_bytes() and
                    (a/'audit.expected').read_bytes()==(b/'audit.expected').read_bytes(),'普通目录轨迹不一致')
            for folder in (a,b):require('键数=1024' in (folder/'verify.log').read_text(),'普通目录全键校验缺失')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='YCSB重链接及四次普通目录自测通过，构建客机已关闭')

    def arm(self,stage,mode):
        from analyze_stable_ycsb import sizes, analyze_arm
        tag=stage+'-'+mode;ops=sizes(stage)[1]
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-sst-results/'+tag
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw)==expected,'业务前同库身份不一致')
            self.root(f'nohup setsid bash {self.target}/run_stable_ycsb_guest.sh {tag} {mode} {ops} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：不限速真实YCSB及全键校验',客机结果目录=remote,计划业务请求数=ops)
            self.observe(tag)
        finally:self.finish(out,remote)
        result=analyze_arm(out/tag,stage,mode)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'数据/评分/维护核验通过，已归档关机',最近结果={n:result[n]
            for n in ('ZC次数','RR次数','总迁移字节','总成功ZoneReset','吞吐量')})


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);require(CLIENT_MANIFEST.is_file(),'缺继承客户端身份')
    for stage in STAGES:
        for mode in ('greedy','stable'):
            tag=stage+'-'+mode
            for p in (IMAGE_DIR/f'{args.label}-{tag}.qcow2',Path(f'/tmp/{args.label}-{tag}.pid'),
                      Path(f'/tmp/{args.label}-{tag}.qmp'),Path(f'/tmp/{args.label}-{tag}-ssh.sock')):
                require(not p.exists(),'新路径已存在，拒绝覆盖：'+str(p))
    if args.check:print('停止新模型父镜像、锁页/串行预算/独立路径通过；未启动');return
    password=getpass.getpass('不限速YCSB隔离客机认证（仅驻留内存）：')
    output.mkdir();(output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='冻结当前稳定收益模型，与同安全路径单victim Greedy进行真实不限速YCSB配对',
        父镜像=str(PARENT),阶段=['build',*STAGES],模式=['greedy','stable'],
        请求='小ZNS8192键/8192请求；短对照491520键/400000请求；较长对照相同491520键/4000000请求',
        负载='同种子20261017、单线程、50/50、读[0,4096)、更新[0,65536)、4KiB字段、无WAL/无块缓存；两臂相同请求，逻辑重叠非物理共置保证',
        限速='limit.ops=0，无limit.file，不设置RateLimiter；不靠限速拉长观察',
        平台='4GiB/32个128MiB区/128块每区/RR1024/GC20%/原候选资格及安全复核，非32GiB全规模',
        指标='总迁移/ZoneReset与映射推导块擦除、RR/ZC、真实API平均及P99/P99.9/P99.99、Run吞吐；不把客户端吞吐当原始设备IOPS',
        验收='独立重链接及四次普通目录自测；两臂小ZNS、短对照全部通过才继续长对照；原始请求/最终值/配置/库身份配对，逐行重算实际评分',
        收尾='默认一台串行，归档后正常关机及SSH退出；无进展180秒/单臂1500秒则采证关机并停止，无自动重跑',
        边界='单轮，不预设收益或稳态；各臂新NAND/RC，qcow2非NAND快照，初始SST布局不保证逐物理页相同'),ensure_ascii=False,indent=2))
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
    run=StableYcsbRun(args,password,output,profile)
    try:
        run.build()
        from analyze_stable_ycsb import compare_pair
        for stage in STAGES:
            for mode in ('greedy','stable'):run.arm(stage,mode)
            result=compare_pair(output,stage)
            with (output/(stage+'配对核验.json')).open('x') as stream:
                json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=stage+'同库/同请求配对验收通过，两臂均已归档关闭',最近差值=result['模型相对Greedy差值'])
        run.record(阶段='不限速小测、短对照及较长对照全部完成，客机已关闭，等待维护覆盖与效果分析')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:run.password=None


if __name__=='__main__':main()
