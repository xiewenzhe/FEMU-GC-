#!/usr/bin/env python3
"""复用停止的持续读同库，串行比较Greedy/旧窗口/新修正；不改变模型或自动启动YCSB。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest
from run_persistent_credit_host import PersistentRun
from run_window_credit_host import resources,digest
from analyze_persistent_credit import analyze_pair

LABEL='joint-persistent-cases-v1-20261022'
SOURCE=IMAGE_DIR/'joint-persistent-credit-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-persistent-credit-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
CASES=('burst-stopped','eligible-cost','retire')
MODES=('greedy','window','persistent')
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_persistent_cases_guest.sh','run_persistent_credit_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','test_persistent_credit_selector.cc','joint_read_trace.h')]
FILES += [PROJECT/'research/rrzc/model/rrzc_recent_sampler.h']


def checked_source():
    state=json.loads((SOURCE/'状态.json').read_text())
    require('build' in state['已完成'],'原持续读构建没有完成')
    require(not Path('/proc/'+str(state['监督进程PID'])).exists(),'原监督仍在运行')
    if state.get('当前客机PID'):
        require(not Path('/proc/'+str(state['当前客机PID'])).exists(),'原失败客机尚未退出')
    archive=json.loads((SOURCE/'build/阶段归档.json').read_text())
    require(archive['完整'] and not archive['客机仍存活'],'原构建归档或退出不完整')
    built=SOURCE/'build'/PARENT.stem.removesuffix('-build')
    require((built/'build.exit').read_text().strip()=='0','原构建退出失败')
    evidence=json.loads((SOURCE/'build'/(built.name+'.归档核验.json')).read_text())
    actual={str(p.relative_to(built)):digest(p) for p in built.rglob('*') if p.is_file()}
    require(evidence['核验通过'] and actual==evidence['文件SHA256'],'原构建归档哈希变化')
    expected=json.loads(MANIFEST.read_text());plan=json.loads((SOURCE/'预注册计划.json').read_text())
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==plan['FEMU二进制SHA256'],'FEMU二进制已漂移')
    require(json.loads((SOURCE/'平台配置.json').read_text())==base.controlled_profile(),'平台配置漂移')
    for name in ('rrzc_persistent_credit_selector.h','rrzc_window_credit_selector.h','rrzc_v1_growth.h'):
        require(digest(PROJECT/'research/rrzc/model'/name)==expected['plugin/zenfs/fs/'+name],
            '不能静默修改已编译模型：'+name)
    return expected


class CaseRun(PersistentRun):
    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        dependencies=[Path(__file__).resolve(),TESTS/'run_persistent_credit_host.py',
            TESTS/'run_window_credit_host.py',TESTS/'run_joint_host.py',TESTS/'run_rr_fair_long_v2_host.py',
            TESTS/'run_rr_fair_long_host.py',TESTS/'run_rr_fair_ycsb_host.py',TESTS/'run_stable_ycsb_host.py',
            TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_tail_ablation_smoke_pair.py',TESTS/'run_stream_pair.py']
        for p in FILES+dependencies:(frozen/p.name).write_bytes(p.read_bytes())
        analysis=frozen/'分析';analysis.mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(analysis/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:digest(p) for p in FILES+dependencies},ensure_ascii=False,indent=2))
        expected=checked_source();out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'实际父镜像软件与已构建身份不同')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_persistent_cases_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='仅编译新受控场景，冻结原库、模型和RR路径')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/program-build.log || tail -1 {self.target}/program-build.log',300)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','场景程序构建失败，停止，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        require(all(software.get(n)==v for n,v in expected.items()),'原库、模型、原程序或客户端发生变化')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='场景编译及原库不变核验通过，构建客机已归档关闭')


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile);checked_source()
    for c in CASES:
        for m in MODES:
            for p in (IMAGE_DIR/f'{args.label}-{c}-{m}.qcow2',Path(f'/tmp/{args.label}-{c}-{m}.pid'),
                Path(f'/tmp/{args.label}-{c}-{m}.qmp'),Path(f'/tmp/{args.label}-{c}-{m}-ssh.sock')):
                require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    if args.check:print(json.dumps(dict(只读预检通过=True,未启动客机=True,资源=before),ensure_ascii=False));return
    password=getpass.getpass('区分场景隔离客机认证（仅驻留内存）：')
    output.mkdir();qemu_digest=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='验证新增持续性因素的作用与代价，不以必须改善为验收标准',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=qemu_digest,场景=list(CASES),模式=list(MODES),
        初始资源=before,场景准备='原24%空闲预热及928次真实同块读，原填充自然降至17%；不调用GC或RR函数',
        停止场景='burst-stopped：冷4MiB热5MiB；十秒读增量仍为928，但最近同块0.5至2秒无增长；释放后0次读',
        空间场景='eligible-cost：冷4MiB热10MiB均须原合格且安全；后续16次每秒共128次读',
        退役场景='retire：沿用冷4MiB热8MiB，第4秒退役；只读64次；不将未来退役时刻传给模型',
        资格验收='新场景首轮实际评分必须同时包含两个安全原合格源；否则场景失败，不声称验证成本权衡',
        不变='同库三策略，4GiB/RR1024/GC20%/原资格及安全路径/模型公式/原RR公平队列均不改',
        收尾='复用已验收QMP后延长正常退出等待，核验账号与镜像；先哈希归档再关机及SSH退出，失败停止不强杀',
        风险='空间场景可能显示更保守的ETA错失可省RR；退役场景也可能仍有负收益；必须如实保留',
        边界='固定25秒功能观察，不是YCSB，不代表稳态或吞吐；旧失败结果保持不变；本入口不自动长测'),ensure_ascii=False,indent=2))
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
    run=CaseRun(args,password,output,profile);run.qemu_digest=qemu_digest
    run.state['模式顺序']=[c+'-'+m for c in CASES for m in MODES]
    try:
        run.build()
        for scenario in CASES:
            for mode in MODES:run.arm(scenario,mode)
            result={m:analyze_pair(output,scenario,model=m) for m in ('window','persistent')}
            a=result['window']['直接模型'];b=result['persistent']['直接模型']
            result['修正相对旧窗口差值']={k:b[k]-a[k] for k in ('RR次数','ZC次数','总复制字节','总成功ZoneReset')}
            with (output/(scenario+'三策略核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=scenario+'三策略同初态/读取/评分/数据验收通过，客机均关闭',最近差值=result['修正相对旧窗口差值'])
        run.record(阶段='三个区分场景完成，全部客机退出，尚未运行新YCSB')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
