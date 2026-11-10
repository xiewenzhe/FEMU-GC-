"""原G2与W历史准入单轮串行对照；同程序、同输入、独立临时客机。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
from types import SimpleNamespace

import run_placement_wmig_pair_host as common
from analyze_placement_survivor import MODE, analyze, finish
from calibration_source import once
from placement_business_source import wrapper as business_wrapper

BUILD_LABEL = 'joint-controlled-placement-wmig-ws-b2-20261109'
PAIR_LABEL = 'joint-controlled-placement-wmig-ws-p1-20261109'


def wrapper(text):
    return once(business_wrapper(text), '( $1 == G0 || $1 == G1 || $1 == G2 )',
                '( $1 == G2 || $1 == G2_WSURVIVOR )')


def check_label(label):
    common.require(label.startswith('joint-controlled-placement-wmig-ws-') and
        all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in label), '配对标签非法')
    for mode in ('G2', MODE):
        socket = '/tmp/' + label + '-' + mode.lower().replace('_', '-') + '-calibration-ssh.sock'
        common.require(len(socket.encode()) + 17 < 108, '配对SSH临时套接字路径过长')


def configure():
    common.MODE = MODE
    common.MODES = ('G2', MODE)
    common.PROGRAM = common.IMAGE_DIR / (BUILD_LABEL + '-results')
    common.analyze = analyze
    common.finish = finish
    common.wrapper = wrapper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=PAIR_LABEL)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    check_label(args.label);configure()
    common.wait_for_slot()
    resource = common.preflight()
    used = common.check_baseline_budget(8 << 30)
    common.require(resource['data可用字节'] >= 42 << 30 and resource['可用内存字节'] >= 30 << 30,
                   '原配对资源保护线不足')
    proof = json.loads((common.PROGRAM / '设备集成核验.json').read_text())
    extra = json.loads((common.PROGRAM / 'W历史设备补充核验.json').read_text())
    common.require(proof['通过'] and proof['模式'] == MODE and extra['通过'] and
        extra['状态序列'] == [0, 2, 1], '先通过连续迁移与恢复未知的真实设备核验')
    if args.check:
        print(json.dumps(dict(资源=resource,目录已占字节=used,设备核验通过=True),ensure_ascii=False));return
    output = common.IMAGE_DIR / (args.label + '-results')
    common.require(not output.exists(), '不覆盖旧配对结果')
    password = getpass.getpass('W历史准入配对认证（仅内存）：')
    output.mkdir();freeze = output / '入口源码';freeze.mkdir()
    files = [Path(__file__), Path(__file__).with_name('placement_survivor_source.py'),
        Path(__file__).with_name('run_placement_wmig_pair_host.py'),
        Path(__file__).parents[1] / 'model/rrzc_write_survivor.h',
        Path(__file__).parents[1] / 'analyze_placement_survivor.py',
        Path(__file__).parents[1] / 'analyze_placement_business.py']
    for file in files: shutil.copyfile(file, freeze / file.name)
    common.stamp(freeze / 'SHA256.json', {p.name:common.digest(p) for p in freeze.iterdir()})
    common.stamp(output / '预注册计划.json', dict(模式=['G2', MODE], 平台=common.profile8(),
        K=1, RR=1024, GC启动百分比=20, 选择器='两臂Greedy，不修改候选或Credit/Score',
        请求数=2260992, 读=786432, 写=1474560, 种子=20261023, 缓存=False, WAL=False,
        串行独立客机=True, 额外Greedy重复=False, 无QPS限速=True, 组成观测共同启用=True,
        历史='同一挂载内成功片段迁移；恢复未知，不写磁盘格式',
        比较边界='先核对规则实际命中和此前布局分歧，不把拒绝量等同节省；末端空间及普通区后续成本单列',
        资源=resource,目录已占字节=used))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not common.detach_supervisor(output): return
    try:
        common.execute(output, SimpleNamespace(label=args.label,prepared_source=None), password)
        common.record(output,阶段='G2与W历史准入配对完成，均已归档退出',完整实验完成=True,当前客机PID=None)
    except BaseException as error:
        common.record(output,阶段='本轮停止推进，保留失败原始证据',原始错误=repr(error));raise
    finally: password = None


if __name__ == '__main__': main()
