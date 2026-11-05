#!/usr/bin/env python3
"""第二版真实留存机会与持续存活负例；同量后续写入分账，全部串行收尾。"""
import argparse,getpass,json,signal
from pathlib import Path
import run_joint_host as base
from run_joint_host import PROJECT,IMAGE_DIR,require,digest_manifest
from run_terminal_host import TerminalRun
from run_window_credit_host import resources,digest
from analyze_joint_followup import analyze_arm,compare
from analyze_joint_opportunity import fields
from replay_terminal import inputs

LABEL='joint-terminal-v2-20261023'
SOURCE=IMAGE_DIR/'joint-terminal-v1-20261023-results'
PARENT=IMAGE_DIR/'joint-terminal-v1-20261023-build.qcow2'
MANIFEST=SOURCE/'同库身份.json';ARCHIVE=SOURCE/'build/joint-terminal-v1-20261023'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
CASES=('stable','aging');MODES=('space','joint')

class TerminalV2Run(TerminalRun):
    files=[TESTS/n for n in ('build_terminal_v2_guest.sh','test_terminal_v2.cc','joint_followup.h',
        'joint-followup-client.patch','terminal-v2-client.patch','terminal-v2-run.patch')]
    files += [ROOT/'model/rrzc_terminal_selector.h',ROOT/'model/rrzc_terminal_v2.h',ROOT/'zenfs-terminal-v2.patch',Path(__file__).resolve()]
    parent_files=[ARCHIVE/n for n in ('joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','joint_read_trace.h','strict_stats_guest.py')]
    parent_files += [IMAGE_DIR/'joint-time-consistent-v1-20261023-results/build/joint-time-consistent-v1-20261023/run_joint_time_guest.sh']
    manifest=MANIFEST
    build_script='build_terminal_v2_guest.sh'

    def build(self):
        super().build()
        actual=json.loads((self.output/'同库身份.json').read_text())
        require(actual['plugin/zenfs/fs/rrzc_terminal_v2.h']==digest(self.output/'源码/rrzc_terminal_v2.h'),'第二版模型部署身份不符')

    def arm(self,case,mode):
        tag=case+'-'+mode;out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/opportunity'
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,'运行前软件身份不符')
            self.root(f'nohup setsid bash {self.target}/run_joint_time_guest.sh {mode} opportunity {case} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实同块读取及已发生留存历史，同量追加128MiB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',420)
        finally:self.finish(out,remote)
        root=out/'opportunity';result=analyze_arm(root,mode,terminal=True,terminal_version=2)
        require((root/'负载.txt').read_text()==f'留存负载={case}\n先验退役开关={int(case=="aging")}\n','实际留存负载不符')
        lines=(root/'zenfs.run.log').read_text().splitlines()
        prior=[i for i,s in enumerate(lines) if '留存先验退役：' in s]
        later=[i for i,s in enumerate(lines) if '留存后续退役：' in s]
        if case=='stable':require(not prior and not later,'持续存活负例混入退役')
        else:
            release=[i for i,s in enumerate(lines) if '联合维护释放：' in s]
            end=[i for i,s in enumerate(lines) if '联合维护观察结束：' in s]
            require(len(prior)==len(later)==len(release)==len(end)==1 and prior[0]<release[0]<later[0]<end[0],
                    '留存先验与未来退役边界不完整')
            evidence=fields(lines[prior[0]],'留存先验退役：')
            require(evidence==dict(冷源=result['初态'][0]['源Zone'],已删除字节=str(8<<20),仍有效字节=str(4<<20),发生于选区之前='1'),
                    '先验退役并非真实同区12MiB降至4MiB')
            require('冷文件退役校验通过：' in (root/'verify.stdout.log').read_text(),'退役冷文件存在性未校验')
        needs,rows=inputs(lines);first=next(p for p in result['空间方案'] if p['实际Zone'] is not None)
        cold=next(r for r in rows[first['轮次']] if r['z']==int(result['初态'][0]['源Zone']))
        if case=='aging' and mode=='joint':
            require(cold['known'] and cold['terminal']<4<<20,'没有真实留存变化进入模型，不能称联合机会验收通过')
        result.update(留存负载=case,首轮冷区留存=cold,
            边界='第二版真实受控配对；空间对照关闭RR/留存评分管线，不是单参数严格消融；非YCSB')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'数据、留存、第二版取整及维护分账通过，客机关闭',最近结果=result['全业务'])
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--label',default=LABEL)
    a=p.parse_args();a.phase='build';a.scenarios=[];a.extend_build_root=False
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    out,profile=base.preflight(a);before=resources(profile)
    require(json.loads((SOURCE/'状态.json').read_text())['当前客机PID'] is None,'父实验仍未收尾')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU二进制漂移')
    for case in CASES:
        for mode in MODES:
            tag=a.label+'-'+case+'-'+mode
            for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
                require(not path.exists(),'拒绝覆盖目标：'+str(path))
    if a.check:print(json.dumps(dict(只读预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('取整一致留存配对认证（仅内存）：');out.mkdir()
    (out/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (out/'预注册计划.json').write_text(json.dumps(dict(目的='真实验证边界取整一致和可观察留存机会，不预设收益',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        场景=list(CASES),模式=list(MODES),不变='4GiB/32区/128MiB区/128块、严格RR1024、GC20%、原候选及共享安全',
        接线='边界配置版本2、复制统一向上取整；首版及所有旧模型头不覆盖',
        负载='真实初态冷4热5MiB/热RC928/空闲18%，128次16/s读，25秒后同写128MiB再等25秒',
        留存='aging同冷区预放8MiB并在选区前真实删除，模型只观察已发生变化；释放后第8秒退役剩余冷4MiB',
        对照='同库同负载；space关闭RR/留存评分管线，本受控二候选同容量下等价有效量Greedy；不是stock或严格单参数消融',
        验收='新库实际编译、真实窗口留存进入评分、分阶段账目与终态空间、候选及追加载荷全量验证',
        收尾='逐组串行，归档哈希后正常关机与PID/SSH核验，失败不自动重跑；非新YCSB性能结论'),ensure_ascii=False,indent=2))
    run=TerminalV2Run(a,password,out,profile);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build()
        for case in CASES:
            arms={mode:run.arm(case,mode) for mode in MODES}
            require((out/(case+'-space')/'opportunity/software.before.log').read_bytes()==
                (out/(case+'-joint')/'opportunity/software.before.log').read_bytes(),'两组实际软件不一致')
            result=compare(arms);result['留存负载']=case
            (out/(case+'配对核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            run.record(阶段=case+'配对完成并独立分析，全部客机关闭',最近差值=result['全业务差值'])
        run.record(阶段='第二版持续存活与真实留存四组完成并分析，全部客机关闭；未运行新YCSB')
        print('两类同量后续写入配对已完成：'+str(out),flush=True)
    except BaseException as error:run.record(阶段='第二版实验停止，保留原始错误，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
