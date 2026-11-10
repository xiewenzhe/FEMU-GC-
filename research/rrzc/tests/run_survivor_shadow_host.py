"""同一旧G2建议观察下串行构建/配对；沿用镜像和结果生命周期。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
from types import SimpleNamespace

import run_placement_wmig_build_host as build_entry
import run_placement_survivor_pair_host as pair_entry
from run_placement_survivor_build_host import verify_history
from placement_survivor_shadow_source import MODE,prepare

BUILD_LABEL='joint-controlled-placement-wmig-ws-sb1-20261109'
PAIR_LABEL='joint-controlled-placement-wmig-ws-sp1-20261109'


def freeze(output):
    directory=output/'影子入口源码';directory.mkdir()
    for path in [Path(__file__),Path(__file__).with_name('placement_survivor_shadow_source.py'),
                 Path(__file__).with_name('test_survivor_shadow.py'),
                 Path(__file__).parents[1]/'analyze_survivor_shadow.py']:
        if path.exists():shutil.copyfile(path,directory/path.name)
    build_entry.stamp(directory/'SHA256.json',{p.name:build_entry.digest(p) for p in directory.iterdir()})


def run_build(args):
    pair_entry.check_label(args.label)
    resource=build_entry.preflight();resource['目录已占字节']=build_entry.check_baseline_budget(8<<30)
    build_entry.require(resource['data可用字节']>=42<<30 and resource['可用内存字节']>=30<<30,'原保护线不足')
    if args.check:print(json.dumps(resource,ensure_ascii=False));return
    output=build_entry.IMAGE_DIR/(args.label+'-results')
    build_entry.require(not output.exists(),'不覆盖旧构建')
    password=getpass.getpass('旧G2影子观察独立构建认证（仅内存）：')
    output.mkdir();build_entry.prepare_source=prepare;payload=build_entry.prepare(output);freeze(output)
    build_entry.stamp(output/'预注册计划.json',dict(模式=MODE,算法未调整=True,
        影子='拒绝时只读Choose；不Acquire目标/不申请令牌/不Commit',
        开销='拒绝路径多一次RC和原生状态采集；非零开销，不混用旧性能',资源=resource))
    if args.detach and not build_entry.detach_supervisor(output):return
    try:
        build_entry.execute(SimpleNamespace(label=args.label,mode=MODE,reuse_program=None),password,output,payload)
        verify_history(output,args.label)
        from analyze_survivor_shadow import parse_shadow
        local=output/args.label/'历史准入功能'
        records=[]
        for name in ['history.zenfs.log','recovered.zenfs.log']:records.extend(parse_shadow((local/name).read_text()))
        build_entry.require(len(records)==3 and [x['历史'] for x in records]==[0,2,1], '设备三次影子缺失')
        build_entry.require(all(x['可知'] for x in records),'设备影子有未知建议')
        build_entry.stamp(output/'影子设备核验.json',dict(通过=True,逐次=records,未验证业务收益=True))
    finally:password=None


def run_pair(args):
    from analyze_survivor_shadow import analyze,finish
    pair_entry.BUILD_LABEL=BUILD_LABEL
    # pair入口内部重设common，因此替换其configure只在此进程生效。
    configure=pair_entry.configure
    def configured():
        configure();pair_entry.common.analyze=analyze;pair_entry.common.finish=finish
        execute=pair_entry.common.execute
        def executed(output,native,password):
            freeze(output)
            return execute(output,native,password)
        pair_entry.common.execute=executed
    pair_entry.configure=configured
    proof=build_entry.IMAGE_DIR/(BUILD_LABEL+'-results')/'影子设备核验.json'
    build_entry.require(json.loads(proof.read_text())['通过'],'先通过影子设备核验')
    import sys
    original=sys.argv
    sys.argv=[__file__,'--label',args.label]+(['--detach'] if args.detach else [])+(['--check'] if args.check else [])
    try:pair_entry.main()
    finally:sys.argv=original


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['build','pair'],required=True)
    parser.add_argument('--label');parser.add_argument('--detach',action='store_true');parser.add_argument('--check',action='store_true')
    args=parser.parse_args();args.label=args.label or (BUILD_LABEL if args.stage=='build' else PAIR_LABEL)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    if args.stage=='build':run_build(args)
    else:run_pair(args)


if __name__=='__main__':main()
