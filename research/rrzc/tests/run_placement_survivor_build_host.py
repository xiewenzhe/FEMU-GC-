"""独立W历史准入全量构建入口；先守住资源保护线，再认证及启动客机。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
from types import SimpleNamespace

import run_placement_wmig_build_host as common
from placement_survivor_source import MODE, prepare

LABEL = 'joint-controlled-placement-wmig-ws-b1-20261109'


def verify_history(output, label):
    local = output / label
    history = (local / '历史准入功能/history.zenfs.log').read_text()
    recovered = (local / '历史准入功能/recovered.zenfs.log').read_text()
    states = [int(x) for x in re.findall(r'W历史准入评估：对象=\d+，历史状态=(\d+)', history)]
    after = [int(x) for x in re.findall(r'W历史准入评估：对象=\d+，历史状态=(\d+)', recovered)]
    common.require(states == [0, 2] and after == [1], '真实设备历史状态不是首次/再次/恢复未知')
    common.require(history.count('W历史准入回退：') == 1 and 'W历史准入回退：' not in recovered,
                   '真实设备重复W拒绝或恢复后回退不符')
    common.require('W历史设备完整路径通过' in (local / 'W历史连续迁移设备测试.log').read_text(),
                   '三次迁移的内容和删除未通过')
    composition = common.composition_audit(local / '历史组成.bin')
    common.require(composition['有效复制字节'] == 3 << 20 and len(composition['复制明细']) == 3,
                   '连续历史迁移复制账目不符')
    targets = [t for t in composition['目标'] if t['类型'] == '实际目标' and t['路径'] == '必要迁移']
    common.require(len(targets) == 3, '三次W迁移实际目标缺失')
    pooled = []
    for target in targets:
        attempt = max(a['事件'] for a in composition['协同尝试'] if
                      a['对象'] == target['对象'] and a['事件'] < target['事件'])
        pooled.append(any(t['协同目标'] and t['对象'] == target['对象'] and
                          attempt < t['事件'] < target['事件'] for t in composition['目标']))
    common.require(not pooled[1], '再次W迁移仍取得协同目标')
    common.require(all(t['标签'] == 2 for t in targets), '历史测试载荷已变H，不能验证W-only门控')
    proof = dict(通过=True, 状态序列=[*states, *after], 再次迁移跳过协同=True,
        三次迁移及内容校验=True, 实际复制字节=3 << 20, 重挂载恢复未知=True,
        磁盘格式未改变=True, 目标=targets, 目标实际是否入池=pooled, 组成核验=composition,
        边界='受控必要迁移接口功能验证，不是业务收益实验；失败/部分提交仍由本地状态和真实入口测试覆盖')
    common.stamp(output / 'W历史设备补充核验.json', proof)
    return proof


def check_label(label):
    common.require(label.startswith('joint-controlled-placement-wmig-ws-') and
        all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in label), '构建标签非法')
    path = '/tmp/' + label + '-calibration-ssh.sock'
    common.require(len(path.encode()) + 17 < 108, 'SSH临时控制套接字路径过长')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    check_label(args.label)
    resource = common.preflight()
    resource['目录已占字节'] = common.check_baseline_budget(8 << 30)
    common.require(resource['data可用字节'] >= 42 << 30 and resource['可用内存字节'] >= 30 << 30,
                   '维持原有独立构建保护线')
    if args.check:
        print(json.dumps(resource, ensure_ascii=False, indent=2));return
    output = common.IMAGE_DIR / (args.label + '-results')
    common.require(not output.exists(), '不覆盖旧构建结果')
    password = getpass.getpass('W历史准入独立构建认证（仅内存）：')
    output.mkdir()
    common.prepare_source = prepare
    payload = common.prepare(output)
    freeze = output / '入口源码';freeze.mkdir()
    for path in [Path(__file__), Path(__file__).with_name('placement_survivor_source.py'),
                 Path(__file__).parents[1] / 'model/rrzc_write_survivor.h']:
        shutil.copyfile(path, freeze / path.name)
    common.stamp(freeze / 'SHA256.json', {p.name: common.digest(p) for p in freeze.iterdir()})
    common.stamp(output / '预注册计划.json', dict(模式=MODE, K=1, 全量独立编译=True,
        历史='仅本会话成功片段迁移映射，不持久化', 非业务收益实验=True, 资源=resource,
        验收边界='通用7MiB迁移及独立三次1MiB W必要迁移，含恢复未知、完整内容和删除校验'))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not common.detach_supervisor(output): return
    try:
        common.execute(SimpleNamespace(label=args.label, mode=MODE, reuse_program=None), password, output, payload)
        verify_history(output, args.label)
    finally: password = None


if __name__ == '__main__': main()
