"""只读归因W历史准入的实际去向、分类复制及首次分歧；不推导反事实节省。"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from analyze_hotfallback_costs import reconstruct
from analyze_placement_trace import rows

MODES = ('G2', 'G2_WSURVIVOR')


def save_csv(path, records):
    if not records:
        return
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader();writer.writerows(records)


def first_difference(left, right, fields):
    for index, (a, b) in enumerate(zip(left, right)):
        if any(a[key] != b[key] for key in fields):
            return dict(序号从一开始=index + 1,
                各臂={MODES[0]: a, MODES[1]: b})
    return None if len(left) == len(right) else dict(数量不同=[len(left), len(right)])


def analyze(root, output):
    output.mkdir(parents=True, exist_ok=True)
    data = {mode: reconstruct(root, mode) for mode in MODES}
    save_csv(output / '逐笔复制分类.csv', [copy for mode in MODES for copy in data[mode][2]])
    save_csv(output / '逐次维护分类.csv', [m for mode in MODES for m in data[mode][3]])
    report, raw, copies, _, _ = data[MODES[1]]
    gates = [x for x in report['W历史准入']['逐次'] if x['拒绝协同']]
    retired, cleared, timestamps, boundaries = defaultdict(list), defaultdict(list), {}, []
    for row in rows(raw / '组成.bin'):
        seq, ns, _, kind, _, owner, _ = row[:7]
        timestamps[seq] = ns
        if kind == 16:
            retired[owner].append(seq)
        elif kind == 4:
            cleared[owner].append(seq)
        elif kind == 27:
            boundaries.append(dict(事件=seq, 阶段=row[7]))
    cutoff = max(x['事件'] for x in boundaries if x['阶段'] == 2)
    linked = []
    for gate in gates:
        copy = next(c for c in copies if c['组成事件'] == gate['复制事件'])
        target = next(t for t in reversed(report['组成']['目标']) if t['类型'] == '实际目标' and
                      t['对象'] == gate['对象'] and gate['尝试事件'] < t['事件'] < copy['组成事件'])
        after = [c for c in copies if c['文件编号'] == gate['文件编号'] and c['组成事件'] > copy['组成事件']]
        following = [t for t in report['维护及资源']['事务'] if t['Zone'] == copy['目标Zone'] and
                     t['源代际'] == copy['目标代际'] and t['开始行'] - 1 > copy['原生复制行']]
        retirement = next((s for s in retired[gate['对象']] if copy['组成事件'] < s < cutoff), None)
        # 本轮需同时确认没有后续再迁移；否则Clear不能代表释放同一落点。
        released = next((s for s in cleared[gate['对象']] if retirement is not None and
                         retirement < s < cutoff), None)
        contents = target['当前组成']
        linked.append(dict(文件编号=gate['文件编号'], 对象=gate['对象'],
            既往成功提交事件=gate['本会话先前成功提交事件'], 拒绝尝试事件=gate['尝试事件'],
            原生拒绝行=gate['拒绝日志行'], 本次复制事件=copy['组成事件'],
            原生复制行=copy['原生复制行'], 维护类型=copy['维护类型'],
            源Zone=copy['源Zone'], 源代际=copy['源代际'],
            普通目标Zone=copy['目标Zone'], 普通目标编号=copy['目标Zone'] // (1 << 27),
            目标代际=copy['目标代际'], 实际复制字节=copy['有效复制字节'],
            目标当时H字节=contents['H_only'] + contents['HW'],
            目标当时W字节=contents['W_only'] + contents['HW'],
            目标当时未分类SST字节=contents['未分类SST'],
            后续该文件再复制次数=len(after), 后续该文件再复制字节=sum(c['有效复制字节'] for c in after),
            目标同代际后续维护=';'.join(t['类型'] + ':' + str(t['开始行'] - 1) for t in following),
            业务及收尾内退役事件=retirement, 退役后实际Clear事件=released,
            拒绝到退役秒=None if retirement is None else (timestamps[retirement]-timestamps[copy['组成事件']])/1e9,
            观察边界='仅本轮收尾前；Clear为同对象extent释放事件，映射成功另核验，不推断无限未来'))
    save_csv(output / (str(len(gates))+'次旧W回退后续链.csv'), linked)
    left, right = (data[mode][2] for mode in MODES)
    targets = [[x for x in data[mode][0]['组成']['目标'] if x['类型'] == '实际目标'] for mode in MODES]
    copy_keys = ('维护类型', '源Zone', '源代际', '源片段', '目标Zone', '目标代际', '目标片段', '有效复制字节')
    first_gate = min(gates, key=lambda x: x['尝试事件']) if gates else None
    proof = dict(逐笔分类与原生复制核验通过=True,
        各臂={mode: dict(分类成本=data[mode][4], 原始目录=str(data[mode][1]),
            原始SHA256={name:hashlib.sha256((data[mode][1]/name).read_bytes()).hexdigest()
                for name in ('组成.bin', '等待区间.bin', 'run.zenfs.log', 'run.rocksdb.log')}) for mode in MODES},
        拒绝次数=len(gates), 拒绝文件数=len({g['文件编号'] for g in gates}),
        拒绝后实际复制字节=sum(x['实际复制字节'] for x in linked),
        拒绝后仍再复制次数=sum(x['后续该文件再复制次数'] for x in linked),
        收尾前观察到退役文件数=sum(x['业务及收尾内退役事件'] is not None for x in linked),
        退役后观察到Clear文件数=sum(x['退役后实际Clear事件'] is not None for x in linked),
        首次复制位置差异=first_difference(left, right, copy_keys),
        首次含文件编号的复制差异=first_difference(left, right, (*copy_keys, '文件编号')),
        首次实际目标差异=first_difference(*targets,
            ('文件编号', 'Zone', '代际', '标签', '路径', '剩余可写', '原生引用有效')),
        首次新规则拒绝=first_gate,
        边界='实际复制差额不等于新规则的已隔离因果收益；拒绝后无再复制只覆盖本轮观察窗口。')
    (output / '实际去向与首差核验.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path);parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = analyze(args.root, args.output)
    print(json.dumps({k:v for k,v in result.items() if k != '各臂'},ensure_ascii=False,indent=2))
