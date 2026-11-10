"""复用已验收双目标观察程序，仅跑一对串行采证；不改变运行算法。"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_placement_survivor_pair_host as pair
from calibration_source import once
from analyze_target_shadow_pair import analyze,finish
from analyze_survivor_outcomes import analyze as outcomes

BUILD_LABEL='joint-controlled-placement-wmig-ws-tsb1-20261109'
PAIR_LABEL='joint-controlled-placement-wmig-ws-tsp1-20261110'


def wrapper(text):
    result=pair.wrapper(text)
    return once(result,'ZENFS_RRZC_PLACEMENT_MODE="$placement"',
        'ZENFS_RRZC_PLACEMENT_MODE="$placement" ZENFS_RRZC_TARGET_SHADOW=1')


def freeze(output):
    folder=output/'目标影子入口源码';folder.mkdir()
    for source in [Path(__file__),Path(__file__).parents[1]/'analyze_target_shadow_pair.py',
                   Path(__file__).parents[1]/'analyze_target_shadow.py',
                   Path(__file__).parents[1]/'model/rrzc_target_shadow.h',
                   Path(__file__).parents[1]/'analyze_survivor_outcomes.py']:
        shutil.copyfile(source,folder/source.name)
    pair.common.stamp(folder/'SHA256.json',{p.name:pair.common.digest(p) for p in folder.iterdir()})
    pair.common.stamp(output/'目标影子预注册.json',dict(两臂共同开启=True,
        开关='ZENFS_RRZC_TARGET_SHADOW=1',仅观察=True,策略与阈值不变=True,
        比较='G2与G2_WSURVIVOR，一臂各一次，无额外Greedy重复',
        构建来源=BUILD_LABEL,边界='非原子部分区观察，普通最终目标可能走未知预留/空区后续'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    pair.check_label(PAIR_LABEL)
    build=pair.common.IMAGE_DIR/(BUILD_LABEL+'-results')
    proof=json.loads((build/'目标备选设备核验.json').read_text())
    pair.common.require(proof['通过'],'先通过双目标设备验收')
    pair.BUILD_LABEL=BUILD_LABEL
    original=pair.configure;original_execute=pair.common.execute
    def configured():
        original();pair.common.wrapper=wrapper
        pair.common.analyze=analyze;pair.common.finish=finish
        def execute(output,native,password):
            freeze(output)
            value=original_execute(output,native,password)
            local=Path(__file__).parents[1]/'results/placement-ablation-20261105/阶段六/第十步-同次目标完整采证'
            outcomes(output,local/'实际成本链')
            pair.common.stamp(local/'采证完成.json',dict(通过=True,原始目录=str(output),
                双目标比较=str(output/'配对分析/同次建议与实际目标.csv'),
                客机已退出=True,没有自动新增实验=True))
            return value
        pair.common.execute=execute
    pair.configure=configured
    previous=sys.argv
    sys.argv=[__file__,'--label',PAIR_LABEL]+(['--check'] if args.check else [])+(['--detach'] if args.detach else [])
    try:pair.main()
    finally:
        sys.argv=previous;pair.configure=original;pair.common.execute=original_execute


if __name__=='__main__':main()
