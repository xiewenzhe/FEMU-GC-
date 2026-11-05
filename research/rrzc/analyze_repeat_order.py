#!/usr/bin/env python3
"""Greedy重复性及反向顺序诊断；四组实测不能冒充统计显著性或相同NAND快照。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_all_blocks_dbbench import arm, require

ARMS = (('g1', 'greedy'), ('g2', 'greedy'), ('reverse-model', 'blocks'), ('reverse-greedy', 'greedy'))


def ratio(reference, observed):
    require(reference > 0, '归一化分母必须为正')
    return observed / reference


def metrics(data):
    m = data['业务维护']
    row = dict(ZC次数=m['ZC次数'], RR次数=m['RR次数'], 总迁移MiB=m['总迁移字节']/(1<<20),
               全部成功Reset=m['全部成功Reset次数'], 阶段秒=data['阶段秒'],
               阶段吞吐ops=data['阶段总吞吐ops'], 改选次数=data['改选决策次数'])
    for op in ('READ', 'WRITE'):
        for key in ('平均微秒', 'P99微秒', 'P99.9微秒', 'P99.99微秒'):
            row[op + key] = data['API延迟'][op][key]
    space = data['结束空间']
    row.update(结束空闲MiB=int(space['空闲字节'])/(1<<20),
               结束有效MiB=int(space['有效字节'])/(1<<20),
               结束满区可回收MiB=int(space['满区可回收字节'])/(1<<20))
    return row


def comparisons(rows, prior=None):
    pairs = [('Greedy重复性：第二次/第一次', rows['g1'], rows['g2']),
             ('反向顺序：先模型/后Greedy', rows['reverse-greedy'], rows['reverse-model']),
             ('当前正向：模型/前一Greedy（共用模型运行）',rows['g2'],rows['reverse-model'])]
    if prior:
        pairs.append(('旧版正向历史参考，不与新版合并', metrics(prior['greedy']), metrics(prior['blocks'])))
    keys = ('总迁移MiB', '阶段吞吐ops', 'READ平均微秒', 'WRITE平均微秒')
    return [dict(比较=name, **{k+'比值': ratio(a[k], b[k]) for k in keys}) for name, a, b in pairs]


def analyze(source, prior, output):
    data = {tag: arm(source/tag, mode) for tag, mode in ARMS}
    first = data['g1']
    common = (source/'g1/公共参数.txt').read_bytes()
    for tag, item in data.items():
        require(item['批次请求摘要'] == first['批次请求摘要'], tag+'实际逻辑请求序列不同')
        require((source/tag/'公共参数.txt').read_bytes() == common, tag+'公共参数不同')
    previous = json.loads((prior/'分析/独立核验.json').read_text())
    require(previous['greedy']['批次请求摘要'] == first['批次请求摘要'], '前轮请求序列不同')
    rows = {tag: metrics(value) for tag, value in data.items()}
    result = comparisons(rows, previous)
    output.mkdir(exist_ok=False)
    for name, records in (('四组指标.csv', [dict(组别=k, **v) for k, v in rows.items()]),
                          ('归一化比较.csv', result)):
        with (output/name).open('x', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    with (output/'独立核验.json').open('x') as f:
        json.dump(dict(各组=data, 比较=result), f, ensure_ascii=False, indent=2)
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for tag, _ in ARMS for p in (source/tag).iterdir() if p.is_file()}
    with (output/'分析来源.json').open('x') as f:
        json.dump(hashes, f, ensure_ascii=False, indent=2)
    lines = ['# Greedy重复性与反向顺序诊断', '',
        '四组固定相同操作量，单台8GiB客机串行，每组Reset并重新装载。RR1024、GC20%、无WAL、不限速，模型公式不变。',
        '同一逻辑输入不等于同一SST布局或NAND状态。只有两次控制运行和一组反向配对，不能据此宣称统计显著性。', '',
        '| 组别 | ZC | RR | 迁移MiB | Reset | 吞吐ops/s | 业务秒 | 平均读μs | 平均写μs | 改选 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for tag, r in rows.items():
        lines.append(f"| {tag} | {r['ZC次数']} | {r['RR次数']} | {r['总迁移MiB']:.3f} | {r['全部成功Reset']} | "
            f"{r['阶段吞吐ops']:.2f} | {r['阶段秒']:.3f} | {r['READ平均微秒']:.3f} | {r['WRITE平均微秒']:.3f} | {r['改选次数']} |")
    lines += ['', '| 比较 | 迁移变化 | 吞吐变化 | 平均读变化 | 平均写变化 |',
              '|---|---:|---:|---:|---:|']
    for r in result:
        values = [(r[k+'比值']-1)*100 for k in ('总迁移MiB','阶段吞吐ops','READ平均微秒','WRITE平均微秒')]
        lines.append('| '+r['比较']+' | '+' | '.join(f'{v:+.3f}%' for v in values)+' |')
    lines += ['', 'Greedy重复性一行用于量化两次相同策略的观测差异，不是置信区间；反向配对统一以Greedy为分母。',
        '模型同时与前后Greedy比较，共用同一个模型运行，不能当成两次独立模型重复；修复版与旧版二进制不同，旧版仅历史参考。',
        '若Greedy自身波动与前轮所谓收益相近，或者交换顺序后方向反转，应先承认效果不稳定，不能继续放大RR权重包装收益。',
        '若反向也改善且大于控制差异，只能增加可信度，仍需更多重复及改选文件维护轨迹才能确认因果。',
        'ZC每约10秒检查一次，次数与业务时长耦合；对比迁移同时必须查看结束空间和未回收负担。', '',
        '| 组别 | 结束空闲MiB | 结束有效MiB | 满区待回收MiB |', '|---|---:|---:|---:|']
    for tag, r in rows.items():
        lines.append(f"| {tag} | {r['结束空闲MiB']:.3f} | {r['结束有效MiB']:.3f} | {r['结束满区可回收MiB']:.3f} |")
    lines += ['', '平均及P99、P99.9、P99.99延迟完整保存在四组指标.csv；原始候选、选区、复制提交Reset闭环在独立核验.json。', '']
    with (output/'重复性与顺序分析.md').open('x') as f:
        f.write('\n'.join(lines))
    print(json.dumps(dict(各组指标=rows, 比较=result), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('prior', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    analyze(a.source, a.prior, a.output)
