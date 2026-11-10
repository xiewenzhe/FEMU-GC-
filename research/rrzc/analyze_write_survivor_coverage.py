"""只读核对历史轨迹中的W迁移历史准入覆盖；不是策略重放或节省估计。"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from analyze_placement_trace import rows


def analyze(label, arm):
    report = json.loads((arm / '业务核验.json').read_text())
    state = json.loads((arm / '状态.json').read_text())
    raw = arm / state['标签'] / 'p1'
    native = [(t, c) for t in report['维护及资源']['事务'] for c in t['复制']]
    owners, prior, transactions = {}, {}, defaultdict(list)
    attempts, copies = [], []
    current, retired = {}, set()
    for row in rows(raw / '组成.bin'):
        sequence, _, _, kind, _, owner, _ = row[:7]
        args = row[7:]
        if kind == 2:
            owners[owner] = dict(file=args[0], profile=args[1], sst=args[2] == 1, w=bool(args[3]))
        elif kind == 16:
            retired.add(owner)
        elif kind == 9:
            if args[0] != 2 or args[1]:
                current.pop(owner, None)
                continue
            data = owners[owner]
            record = dict(来源=label, 尝试事件=sequence, 对象=owner, 文件编号=data['file'],
                当前标签=2, 已有提交证据=owner in prior, 既往最近提交事件=prior.get(owner, ''),
                原轨迹实际入池=False, 实际目标='', 目标代际='', 实际复制字节=0,
                复制事件='', 维护类型='', 原生复制行='',
                规则将跳过协同入口=owner in prior)
            current[owner] = record
            attempts.append(record)
        elif kind == 11:
            if owner in current:
                current[owner]['原轨迹实际入池'] = True
        elif kind == 12:
            if owner in current:
                current[owner].update(实际目标=args[0], 目标代际=args[1])
        elif kind == 13:
            transaction, source, source_start, target, target_start, length, generation = args[:7]
            assert len(copies) < len(native)
            maintenance, line = native[len(copies)]
            fields = line['字段']
            assert [source, source_start, target, target_start, length] == [int(fields[k]) for k in
                ('源Zone', '源片段', '目标Zone', '目标片段', '有效字节')]
            assert owners[owner]['file'] == int(fields['文件编号'])
            copies.append(sequence)
            transactions[(owner, transaction)].append(sequence)
            if owner in current:
                record = current.pop(owner)
                assert record['实际目标'] == target and record['目标代际'] == generation
                record.update(实际复制字节=length, 复制事件=sequence,
                    维护类型=maintenance['类型'], 原生复制行=line['行'] - 1)
        elif kind == 14 and args[1] == 1:
            # 原观测仅在SyncFileExtents成功时发此事件；删除不算存活证据。
            if transactions.get((owner, args[0])) and owner not in retired and owners[owner]['w']:
                prior[owner] = sequence
    assert len(copies) == len(native)
    assert all(x['当前标签'] == 2 for x in attempts)
    summary = dict(来源=str(arm), W必要迁移尝试=len(attempts),
        已有成功提交证据的尝试=sum(x['已有提交证据'] for x in attempts),
        原轨迹实际入池次数=sum(x['原轨迹实际入池'] for x in attempts),
        其中新规则覆盖入池次数=sum(x['已有提交证据'] and x['原轨迹实际入池'] for x in attempts),
        原轨迹首次及未知历史入池次数=sum(not x['已有提交证据'] and x['原轨迹实际入池'] for x in attempts),
        规则覆盖入池实际复制字节=sum(x['实际复制字节'] for x in attempts if x['已有提交证据'] and x['原轨迹实际入池']),
        首次覆盖事件=next((x['尝试事件'] for x in attempts if x['已有提交证据']), None),
        原始证据SHA256={name: hashlib.sha256((raw / name).read_bytes()).hexdigest() for name in
            ('组成.bin', 'run.zenfs.log')},
        边界='依原轨迹只核对入口覆盖，规则第一次改变布局后不再构成反事实；字节不能称为节省量。')
    return attempts, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', action='append', required=True, help='来源标签=已归档实验臂目录')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records, summaries = [], {}
    for item in args.input:
        label, path = item.split('=', 1)
        assert label not in summaries
        selected, proof = analyze(label, Path(path))
        records.extend(selected);summaries[label] = proof
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'W历史准入离线覆盖.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader();writer.writerows(records)
    result = dict(各来源=summaries, 已运行新算法业务=False, 未读取未来生命周期作决策=True)
    (args.output / 'W历史准入覆盖核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
