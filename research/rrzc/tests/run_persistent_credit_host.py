#!/usr/bin/env python3
"""在新子镜像编译持续读修正版，串行四类真实短对照；不自动启动YCSB长测。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest
from run_window_credit_host import WindowRun,resources,digest
from run_rr_fair_long_v2_host import CollectedRun
from analyze_window_credit import analyze_pair as source_pair
from analyze_persistent_credit import analyze_arm,analyze_pair

LABEL='joint-persistent-credit-v1-20261022'
SOURCE=IMAGE_DIR/'joint-window-credit-v2-20261021-results'
PARENT=IMAGE_DIR/'joint-window-credit-v2-20261021-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_persistent_credit_guest.sh','run_persistent_credit_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','test_persistent_credit_selector.cc')]
FILES += [PROJECT/'research/rrzc/model/rrzc_persistent_credit_selector.h',
          PROJECT/'research/rrzc/model/rrzc_v1_growth.h',
          PROJECT/'research/rrzc/zenfs-persistent-credit.patch']


def checked_source():
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None,'窗口父实验仍有运行客机')
    for tag in ['build',*[c+'-'+m for c in base.CASES for m in ('greedy','window')]]:
        require(tag in state['已完成'],'源受控阶段未完成：'+tag)
        archive=json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'],'源归档/退出不完整：'+tag)
    plan=json.loads((SOURCE/'预注册计划.json').read_text())
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==plan['FEMU二进制SHA256'],'FEMU版本已变化')
    require(json.loads((SOURCE/'平台配置.json').read_text())==base.controlled_profile(),'受控设备配置变化')
    expected=json.loads(MANIFEST.read_text())
    for name in ('rrzc_window_credit_selector.h',):
        require(digest(PROJECT/'research/rrzc/model'/name)==expected['plugin/zenfs/fs/'+name],
            '原窗口或同块核验已漂移：'+name)
    return {c:source_pair(SOURCE,c) for c in base.CASES}


class PersistentRun(WindowRun):
    # 受控日志直接写入结果目录，无须套用YCSB采证；复用已验收的退出等待及身份核验。
    close_guest=CollectedRun.close_guest
    finish=CollectedRun.finish

    def __init__(self,*args):
        super().__init__(*args)
        self.deployed=False;self.control=None

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        dependencies=[Path(__file__).resolve(),TESTS/'run_window_credit_host.py',TESTS/'run_joint_host.py',
            TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_tail_ablation_smoke_pair.py',TESTS/'run_stream_pair.py',
            TESTS/'run_rr_fair_long_v2_host.py']
        for p in FILES+dependencies:(frozen/p.name).write_bytes(p.read_bytes())
        analysis=frozen/'分析';analysis.mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(analysis/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:digest(p) for p in FILES+dependencies},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'停止父镜像实际身份不符')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_persistent_credit_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='仅新子镜像编译持续读评分及整库，原模型头、RR路径和旧客户端不变')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','新评分或整库编译失败，归档保留，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
        require(all(software.get(n)==v for n,v in expected.items() if n not in changed),'旧评分核、RR队列或客户端改变')
        require(software['librocksdb_debug.a']!=expected['librocksdb_debug.a'],'未确认新整库编译')
        require(software['plugin/zenfs/fs/rrzc_persistent_credit_selector.h']==
            digest(frozen/'rrzc_persistent_credit_selector.h'),'新核与冻结源码不符')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='持续读评分及整库构建通过，已归档且客机退出')

    def arm(self,scenario,mode):
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text());error=None
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,
                '业务前同库身份不符')
            self.root(f'nohup setsid bash {self.target}/run_persistent_credit_guest.sh {mode} {scenario} '
                f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实固定读取及原生ZC/RR，不是YCSB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        except BaseException as cause:
            error=cause;self.record(原始业务或监督错误=type(cause).__name__+'：'+str(cause));raise
        finally:
            try:self.finish(out,remote)
            except Exception as cleanup:
                if error is None:raise
                self.record(收尾另有错误=str(cleanup))
        result=analyze_arm(out/scenario,mode,scenario)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'数据、实际评分及维护账目通过，已归档关闭',
            最近结果={n:result[n] for n in ('ZC次数','RR次数','总复制字节','总成功ZoneReset')})


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='all';args.scenarios=list(base.CASES)
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile);source=checked_source()
    for c in base.CASES:
        for m in ('greedy','persistent'):
            for p in (IMAGE_DIR/f'{args.label}-{c}-{m}.qcow2',Path(f'/tmp/{args.label}-{c}-{m}.pid'),
                      Path(f'/tmp/{args.label}-{c}-{m}.qmp'),Path(f'/tmp/{args.label}-{c}-{m}-ssh.sock')):
                require(not p.exists(),'目标存在，拒绝覆盖：'+str(p))
    if args.check:print(json.dumps(dict(只读预检通过=True,未启动客机=True,资源=before),ensure_ascii=False));return
    password=getpass.getpass('持续读模型隔离客机认证（仅驻留内存）：')
    output.mkdir();qemu_digest=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'源受控复核.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='仅修正持续读ETA；验证能保留真实机会并减少停止读取误判，不以必须改善作为验收条件',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=qemu_digest,
        场景=list(base.CASES),模式=['greedy','persistent'],初始资源=before,
        不变='4GiB/32个128MiB区/每区128块/RR1024/GC20%/原候选资格/额外回收门槛0/共享RR公平队列',
        模型='直接按净复制/回收排序；短窗与十秒或Reset前缀较慢速率决定ETA；留存和支撑窗口公式不变',
        安全='同块身份、会话、Reset和时间单调核验复用；不修改RR、GC或迁移路径，不改变默认旧分支',
        限制='短窗也可能抖动或低估再升温；不预知突然退役；四类受控程序不是YCSB性能实验',
        验收='完整编译和链接、原模型/旧客户端身份不变、同初态同读取、逐条独立整数重算及全载荷校验',
        收尾='默认串行一台，每阶段重查资源，归档集合和哈希复核后正常关闭，PID/SSH退出；失败停止不盲重跑',
        后续='四类验收后再单独部署带阶段计时的YCSB重复配对；本入口不自动长测'),ensure_ascii=False,indent=2))
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
    run=PersistentRun(args,password,output,profile);run.qemu_digest=qemu_digest
    try:
        run.build()
        for scenario in base.CASES:
            for mode in ('greedy','persistent'):run.arm(scenario,mode)
            result=analyze_pair(output,scenario)
            with (output/(scenario+'配对核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=scenario+'配对通过且两臂关闭，不预设正收益',
                最近差值={n:result[n] for n in ('窗口内RR减少','窗口内复制减少字节','窗口内ZoneReset减少')})
        run.record(阶段='持续读修正版四类真实短对照完成，全部客机关闭，尚未运行新YCSB')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
