"""离线核验H回退配对的分类成本、首差和实际分支覆盖，不改变运行结果。"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from analyze_placement_trace import rows

MODES = ('G2', 'G2_HFALLBACK')


def save_csv(path, records):
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def reconstruct(root, mode):
    arm = root / mode
    report = json.loads((arm / '业务核验.json').read_text())
    raw = arm / json.loads((arm / '状态.json').read_text())['标签'] / 'p1'
    owners, heat, generations, copies = {}, {}, {}, []
    clock = 0
    for row in rows(raw / '组成.bin'):
        seq, _, _, kind, _, owner, event_clock = row[:7]
        args = row[7:]
        clock = max(clock, event_clock)
        if kind == 1:
            generations[args[0]] = args[1]
        elif kind == 15:
            assert generations[args[0]] == args[1]
            generations[args[0]] = args[2]
        elif kind == 2:
            owners[owner] = dict(file=args[0], profile=args[1], kind=args[2], w=bool(args[3]))
        elif kind == 8:
            heat[owner] = (bool(args[0]), args[1])
        elif kind == 13:
            data = owners[owner]
            hot, expiry = heat.get(data['profile'], (False, 0))
            hot = hot and clock < expiry
            category = ('非SST' if data['kind'] != 1 else 'HW' if hot and data['w']
                        else 'H_only' if hot else 'W_only' if data['w'] else '未分类SST')
            copies.append(dict(模式=mode, 组成事件=seq, 文件编号=data['file'],
                源Zone=args[1], 源代际=generations[args[1]], 源片段=args[2],
                目标Zone=args[3], 目标代际=args[6], 目标片段=args[4],
                有效复制字节=args[5], 当前类别=category))
    native = [(t, c) for t in report['维护及资源']['事务'] for c in t['复制']]
    assert len(native) == len(copies)
    for copy, (transaction, line) in zip(copies, native):
        for field in ('文件编号', '源Zone', '源片段', '目标Zone', '目标片段'):
            assert copy[field] == int(line['字段'][field])
        assert copy['有效复制字节'] == int(line['字段']['有效字节'])
        copy.update(维护类型=transaction['类型'], 原生复制行=line['行'] - 1,
                    维护开始行=transaction['开始行'] - 1)
    maintenance = []
    rr = 0
    for transaction in report['维护及资源']['事务']:
        rr += transaction['类型'] == 'RR'
        count = Counter()
        for copy in copies:
            if copy['维护开始行'] == transaction['开始行'] - 1:
                count[copy['当前类别']] += copy['有效复制字节']
        assert sum(count.values()) == transaction['迁移字节']
        maintenance.append(dict(模式=mode, 类型=transaction['类型'],
            RR序号=rr if transaction['类型'] == 'RR' else '',
            源区编号=transaction['Zone'] // (1 << 27), 源代际=transaction['源代际'],
            原生开始行=transaction['开始行'] - 1, 总字节=transaction['迁移字节'],
            **{k: count[k] for k in ('H_only', 'W_only', 'HW', '未分类SST', '非SST')}))
    totals = {kind: {key: sum(x[key] for x in maintenance if x['类型'] == kind)
                     for key in ('H_only', 'W_only', 'HW', '未分类SST', '非SST')}
              for kind in ('RR', 'ZC')}
    assert sum(sum(v.values()) for v in totals.values()) == report['维护及资源']['维护']['总迁移字节']
    return report, raw, copies, maintenance, totals


def first_difference(left, right, fields):
    for index, (a, b) in enumerate(zip(left, right)):
        if any(a[key] != b[key] for key in fields):
            return dict(序号从一开始=index + 1, G2=a, G2_HFALLBACK=b)
    if len(left) != len(right):
        return dict(长度不同=[len(left), len(right)])
    return None


def analyze(root, destination):
    destination.mkdir(parents=True, exist_ok=True)
    analyses = {mode: reconstruct(root, mode) for mode in MODES}
    all_copies = [x for mode in MODES for x in analyses[mode][2]]
    all_maintenance = [x for mode in MODES for x in analyses[mode][3]]
    save_csv(destination / '逐笔复制分类证据.csv', all_copies)
    save_csv(destination / '逐次维护分类成本.csv', all_maintenance)
    keys = ('维护类型', '源Zone', '源代际', '源片段', '目标Zone', '目标代际', '目标片段', '有效复制字节')
    a, b = (analyses[mode][2] for mode in MODES)
    target_lists = []
    for mode in MODES:
        target_lists.append([x for x in analyses[mode][0]['组成']['目标'] if x['类型'] == '实际目标'])
    result = dict(逐笔复制与原生日志一致=True,
        各臂={mode: dict(分类成本=analyses[mode][4],
            原始目录=str(analyses[mode][1]),
            原始证据SHA256={name: hashlib.sha256((analyses[mode][1] / name).read_bytes()).hexdigest()
                for name in ('组成.bin', 'run.zenfs.log', 'run.rocksdb.log', '等待区间.bin')}) for mode in MODES},
        首次复制差异含文件编号=first_difference(a, b, (*keys, '文件编号')),
        首次复制位置及字节差异=first_difference(a, b, keys),
        首次实际目标差异含文件编号=first_difference(*target_lists,
            ('文件编号', 'Zone', '代际', '标签', '路径', '剩余可写', '原生引用有效')),
        新分支记录=analyses['G2_HFALLBACK'][0]['H普通回退'],
        边界='当前类别按原H窗口投影与Flush来源重建；首差不是完整因果根因；排序日志不等于相对旧算法改选。')
    (destination / '分类成本与首差核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root, args.destination), ensure_ascii=False, indent=2))
