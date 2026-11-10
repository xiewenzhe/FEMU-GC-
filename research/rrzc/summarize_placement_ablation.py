"""只读三模式既有证据，补充目标取得与实际提交复制的关联，不影响实验策略。"""
import argparse
import hashlib
import json
from pathlib import Path


def pool_copies(composition):
    pending = {}
    actual = {}
    evidence = []
    stream = [(t['事件'], '目标', t) for t in composition['目标']]
    stream += [(c['事件'], '复制', c) for c in composition['复制明细']]
    for _, kind, row in sorted(stream):
        owner = row['对象']
        if kind == '目标':
            if row['协同目标']:
                pending[owner] = row
            else:
                pool = pending.pop(owner, None)
                matched = bool(pool and pool['Zone'] == row['Zone'] and pool['代际'] == row['代际'])
                actual[owner] = (row, pool if matched else None)
        else:
            target, pool = actual.get(owner, (None, None))
            if target is None or target['Zone'] != row['目标Zone']:
                raise ValueError('真实复制缺少对应实际目标事件')
            if pool is None:
                continue
            state = pool['当前组成']
            evidence.append(dict(**row, 文件编号=pool['文件编号'], 路径=pool['路径'],
                标签=pool['标签'], 目标代际=pool['代际'], 目标事件=pool['事件'],
                选择时有效Flush字节=state['W_only'] + state['HW'],
                选择时H已确认=pool['H状态已确认'], 选择时引用数值一致=pool['引用数值一致']))
    hot = [e for e in evidence if e['路径'] == '必要迁移' and e['标签'] & 1]
    return dict(实际入池复制=evidence, H必要迁移实际复制次数=len(hot),
        H必要迁移实际复制字节=sum(e['有效复制字节'] for e in hot),
        H复制前已有有效Flush次数=sum(e['选择时有效Flush字节'] > 0 for e in hot),
        H复制前已有有效Flush字节=sum(e['有效复制字节'] for e in hot if e['选择时有效Flush字节'] > 0),
        H复制提交次数=sum(e['映射结局'] == '提交' for e in hot),
        注='目标组成是状态变化事件的离线重建，非全盘原子快照；已有Flush可能同时带H。取得目标不等于实际复制。')


def analyze(output):
    result = {}
    for mode in ('G0', 'G1', 'G2'):
        path = output / mode / '业务核验.json'
        raw = path.read_bytes()
        report = json.loads(raw)
        if not report['通过']:
            raise ValueError('不能分析未通过的业务结果')
        composition = report['组成']
        changes = composition['组成变化']
        boundary = composition['业务边界'][0]['事件']
        mix = {(c['设备'], c['Zone'], c['代际']) for c in changes if c['事件'] <= boundary
            and c['组成']['H_only'] > 0 and c['组成']['W_only'] > 0}
        pool_zones = {(t['Zone'], t['代际']) for t in composition['目标']
            if t['协同目标'] and t['事件'] <= boundary}
        resets = sum(len(t['Reset']) for t in report['维护及资源']['事务'])
        all_resets = report['维护及资源']['维护']['全部成功Reset次数']
        if resets > all_resets:
            raise ValueError('事务内Reset超过全部Reset')
        result[mode] = dict(来源=str(path), SHA256=hashlib.sha256(raw).hexdigest(),
            维护=report['维护及资源']['维护'], 资源=report['维护及资源']['资源'],
            应用=report['应用端到端'], API=report['时间']['API延迟'],
            时间分账=report['时间']['窗口分账'], 内部区间=report['时间']['内部区间'],
            放置=report['放置事件'], 池复制=pool_copies(composition),
            Reset拆分=dict(维护事务内次数=resets,事务外次数=all_resets-resets,
                说明='事务外含其他回收/元数据路径，不能直接当物理Block擦除次数。'),
            Honly与Wonly曾同时有效的Zone代际数=len(mix),
            其中曾取得协同目标的Zone代际数=sum((z[1], z[2]) in pool_zones for z in mix),
            末端空间={k: v for k, v in report['维护及资源']['末端空间'].items() if k != 'Zone'},
            边界='混放计数是业务结束前曾经同时有效的代际数，不是末端数量或稳定混放时间；单个HW文件不计入此项。')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = analyze(args.output)
    with args.destination.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
