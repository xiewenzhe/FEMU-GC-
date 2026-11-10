"""离线核对H普通回退后的同批陪迁与后续维护；不计算反事实节省量。"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path


def analyze(root, output):
    output.mkdir(parents=True, exist_ok=True)
    records, summaries, evidence = [], {}, {}
    for mode in ('G2', 'G2_HFALLBACK'):
        arm = root / mode
        report = json.loads((arm / '业务核验.json').read_text())
        raw = arm / json.loads((arm / '状态.json').read_text())['标签'] / 'p1'
        native = [(t, c) for t in report['维护及资源']['事务'] for c in t['复制']]
        copies = sorted(report['组成']['复制明细'], key=lambda c: c['事件'])
        assert len(native) == len(copies)
        by_owner = defaultdict(list)
        attempts = defaultdict(list)
        for attempt in report['组成']['协同尝试']:
            attempts[attempt['对象']].append(attempt['事件'])
        for t in report['组成']['目标']:
            if t['类型'] == '实际目标' and t['路径'] == '必要迁移':
                by_owner[t['对象']].append(t)
        linked = []
        for cp, (maintenance, source) in zip(copies, native):
            fields = source['字段']
            assert all(cp[k] == int(fields[k]) for k in ('源Zone', '源片段', '目标Zone', '目标片段'))
            assert cp['有效复制字节'] == int(fields['有效字节'])
            options = [t for t in by_owner[cp['对象']] if t['事件'] < cp['事件']]
            assert options
            target = options[-1]
            assert target['Zone'] == cp['目标Zone']
            preceding = [seq for seq in attempts[cp['对象']] if seq < target['事件']]
            assert preceding
            pool = any(t['对象'] == cp['对象'] and t['Zone'] == target['Zone'] and
                       preceding[-1] < t['事件'] < target['事件'] and t['协同目标']
                       for t in report['组成']['目标'])
            linked.append(dict(copy=cp, maintenance=maintenance, source=source,
                               target=target, pool=pool))
        for index, item in enumerate(linked):
            target, cp, transaction = item['target'], item['copy'], item['maintenance']
            if not (target['标签'] & 1) or item['pool']:
                continue
            following = [x for x in linked[index + 1:] if
                         x['maintenance']['开始行'] == transaction['开始行'] and
                         x['target']['Zone'] == target['Zone'] and
                         x['target']['代际'] == target['代际'] and not (x['target']['标签'] & 1)]
            later = [t for t in report['维护及资源']['事务'] if t['Zone'] == target['Zone'] and
                     t['源代际'] == target['代际'] and t['开始行'] > item['source']['行']]
            composition = target['当前组成']
            records.append(dict(模式=mode, 文件编号=target['文件编号'], 对象=target['对象'],
                维护类型=transaction['类型'], 维护开始行=transaction['开始行'] - 1,
                目标事件=target['事件'], 复制事件=cp['事件'], 原生复制行=item['source']['行'] - 1,
                源Zone=cp['源Zone'], 源代际=transaction['源代际'],
                普通目标=target['Zone'], 目标代际=target['代际'],
                取得时有效引用字节=target['原生引用有效'], 剩余可写字节=target['剩余可写'],
                当前H字节=composition['H_only'] + composition['HW'],
                当前Flush字节=composition['W_only'] + composition['HW'],
                当前未分类SST字节=composition['未分类SST'], H本次复制字节=cp['有效复制字节'],
                同批随后非H入区次数=len(following),
                同批随后非H入区字节=sum(x['copy']['有效复制字节'] for x in following),
                随后维护=';'.join(t['类型'] + ':' + str(t['开始行'] - 1) for t in later),
                随后维护复制字节=sum(t['迁移字节'] for t in later)))
        selected = [x for x in records if x['模式'] == mode]
        summaries[mode] = dict(H普通目标次数=len(selected),
            H普通目标取得时已有有效引用累计=sum(x['取得时有效引用字节'] for x in selected),
            说明='同目标可被多份H引用，上述累计不是去重数据量或节省量；完整逐事件见CSV。',
            H协同回退诊断行=sum('H协同回退资源：' in line for line in (raw / 'run.zenfs.log').read_text().splitlines()))
        evidence[mode] = {name: hashlib.sha256((raw / name).read_bytes()).hexdigest()
                          for name in ('run.zenfs.log', '组成.bin', '等待区间.bin')}
    if records:
        with (output / 'H普通回退生命周期.csv').open('x', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, list(records[0]));writer.writeheader();writer.writerows(records)
    result = dict(逐笔复制和实际目标核验=True, 各臂=summaries, 原始证据SHA256=evidence,
                  边界='只报告实际同批追随和后续维护，不声称相反放置下这些字节必然不搬。')
    (output / 'H普通目标离线核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path);parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root, args.output), ensure_ascii=False, indent=2))
