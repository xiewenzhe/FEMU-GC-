"""只用本轮已验收真实配对生成中文柱状图；缺失、不适用均不冒充零。"""
import argparse
import csv
import json
from pathlib import Path

from ycsb_af import WORKLOADS, LABELS, require, digest


def render(root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib import font_manager
    import numpy as np

    root = Path(root)
    available = []
    for w in WORKLOADS:
        file = root/f'full-{w}-配对核验.json'
        if file.exists():
            pair = json.loads(file.read_text())
            require(pair['配对验收通过'], '未验收配对不能入图')
            available.append((w, pair, file))
    require(available, '尚无正式配对；不生成示例结果图')
    out = root/'图表'
    out.mkdir(exist_ok=True)
    fonts = [f.name for f in font_manager.fontManager.ttflist if 'Noto Sans CJK' in f.name]
    if not fonts:
        fonts = [f.name for f in font_manager.fontManager.ttflist if 'Noto Serif CJK' in f.name]
    require(fonts, '没有可用中文绘图字体，停止而非生成乱码图')
    plt.rcParams.update({'font.family': fonts[0], 'axes.unicode_minus': False,
                         'font.size': 10, 'svg.fonttype': 'none'})
    letters = [w.upper() for w, _, _ in available]
    x = np.arange(len(letters))
    colors = {'space': '#4477AA', 'model': '#EE7733'}
    rows = [{**p['模式'][m], '工作负载': w.upper()} for w, p, _ in available for m in LABELS]
    maintenance = ('总迁移字节', 'RR迁移字节', 'ZC迁移字节', 'RR次数', 'ZC次数',
                   '总成功ZoneReset', '推导物理Block擦除次数', '前台吞吐ops每秒')
    with (out/'原始指标.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=['工作负载', '策略名称', *maintenance,
                    'Run吞吐ops每秒', '前台秒', 'Cleanup秒', '末态硬件可写字节'])
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in ('工作负载', '策略名称', *maintenance, '末态硬件可写字节')} |
                {'Run吞吐ops每秒': r['吞吐量']['平均ops每秒'], '前台秒': r['客户端阶段耗时']['前台请求秒'],
                 'Cleanup秒': r['客户端阶段耗时']['Cleanup秒']})
    with (out/'API延迟.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=['工作负载', '策略名称', '操作', '次数',
                                                    '平均微秒', 'P99微秒', 'P99.9微秒', 'P99.99微秒'])
        writer.writeheader()
        for r in rows:
            for op, values in r['API调用延迟'].items():
                writer.writerow(dict(工作负载=r['工作负载'], 策略名称=r['策略名称'], 操作=op, **values))
    subtitle = f'32 GiB，未锁页，不限速，单轮配对；已完成 {len(available)}/6 种负载'
    created = []
    def finish(fig, name, pdf):
        fig.suptitle(name+'\n'+subtitle, fontsize=13)
        fig.tight_layout(rect=(0, 0.05, 1, 0.9))
        for extension in ('png', 'svg'):
            fig.savefig(out/(name+'.'+extension), dpi=220, bbox_inches='tight')
        pdf.savefig(fig, bbox_inches='tight')
        created.append(name+'.png')
        plt.close(fig)

    def bars(ax, get, title, ylabel, *, logarithmic=False):
        for index, mode in enumerate(LABELS):
            values = [get(pair['模式'][mode]) for _, pair, _ in available]
            heights = [float('nan') if v is None else v for v in values]
            artist = ax.bar(x+(index-0.5)*0.36, heights, 0.36, label=LABELS[mode], color=colors[mode])
            labels = []
            for v in values:
                if v is None:
                    labels.append('')
                elif v >= 100:
                    labels.append(f'{v:.0f}')
                else:
                    labels.append(f'{v:.2f}'.rstrip('0').rstrip('.'))
            ax.bar_label(artist, labels=labels, fontsize=8, padding=2)
        for index in range(len(available)):
            if all(get(available[index][1]['模式'][m]) is None for m in LABELS):
                ax.text(index, 0.04, '不适用', transform=ax.get_xaxis_transform(), ha='center', fontsize=8)
        ax.set_xticks(x, letters)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(axis='y', alpha=0.2)
        ax.set_axisbelow(True)
        if logarithmic:
            ax.set_yscale('log')
        else:
            ax.set_ylim(bottom=0)
        ax.margins(y=0.2)

    with PdfPages(out/'全部柱状图.pdf') as pdf:
        fig, axes = plt.subplots(2, 3, figsize=(13, 7.5))
        specs = [('总迁移字节', '总有效数据迁移量', 'MiB', 1 << 20),
                 ('RR次数', 'RR完成次数', '次', 1), ('ZC次数', 'ZC完成次数', '次', 1),
                 ('总成功ZoneReset', '成功Zone Reset', '次', 1),
                 ('推导物理Block擦除次数', 'Block擦除当量：Reset×128（推导）', '次', 1),
                 ('末态硬件可写字节', '业务末态硬件可写空间', 'MiB', 1 << 20)]
        for ax, (key, title, unit, scale) in zip(axes.flat, specs):
            bars(ax, lambda r, k=key, s=scale: r[k]/s, title, unit)
        axes[0, 0].legend()
        fig.text(0.02, 0.01, '维护含装载、Run与Cleanup；独立数据校验另计。Reset包含自然空区及元数据，不等于RR＋ZC。', fontsize=9)
        finish(fig, '维护开销', pdf)
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
        bars(axes[0], lambda r: r['前台吞吐ops每秒'], '前台吞吐', '逻辑操作/秒')
        bars(axes[1], lambda r: r['吞吐量']['平均ops每秒'], 'Run吞吐（包含Cleanup）', '逻辑操作/秒')
        axes[0].legend()
        fig.text(0.02, 0.01, '不是设备IOPS；两组均含相同生成与审计开销。未锁页，宿主缺页/换页可能干扰性能。', fontsize=9)
        finish(fig, '吞吐量', pdf)
        for op, name in (('READ', '点读'), ('UPDATE', '更新'), ('INSERT', '插入'),
                         ('SCAN', '扫描'), ('READMODIFYWRITE', '完整读改写事务')):
            if not any(op in r['API调用延迟'] for r in rows):
                continue
            fig, axes = plt.subplots(2, 2, figsize=(11, 7))
            for ax, key in zip(axes.flat, ('平均微秒', 'P99微秒', 'P99.9微秒', 'P99.99微秒')):
                bars(ax, lambda r, k=key: r['API调用延迟'].get(op, {}).get(k), key.replace('微秒', ''), '微秒')
            axes[0, 0].legend()
            fig.text(0.02, 0.01, '非裸设备IO延迟；UPDATE包含Get＋Put；完整读改写含事务内生成与审计。极端分位请结合CSV中样本数。', fontsize=9)
            finish(fig, name+'延迟', pdf)
    evidence = {str(file): digest(file) for _, _, file in available}
    (out/'图表来源.json').write_text(json.dumps(dict(输入SHA256=evidence,
        绘图器SHA256=digest(Path(__file__)), 已完成负载=letters, 单轮=True, 未锁页=True,
        图表=created, 限制='缺失负载不入图，不适用操作不记零；未合并跨操作分位或选择有利试次'), ensure_ascii=False, indent=2))
    report = ['# 32GiB A至F实测结果', '', subtitle, '',
              '空间对照不是stock Greedy；当前模型为已验收边界第二版，未接入新支持预测核。', '',
              '| 负载 | RR 空间/模型 | ZC 空间/模型 | 总迁移MiB 空间/模型 | 模型迁移变化 |',
              '|---|---:|---:|---:|---:|']
    for w, p, _ in available:
        a, b = [p['模式'][m] for m in LABELS]
        delta = f'{(b["总迁移字节"]/a["总迁移字节"]-1)*100:+.2f}%' if a['总迁移字节'] else '基数为0，不计算比例'
        report.append(f'| {w.upper()} | {a["RR次数"]}/{b["RR次数"]} | {a["ZC次数"]}/{b["ZC次数"]} | '
                      f'{a["总迁移字节"]/2**20:.3f}/{b["总迁移字节"]/2**20:.3f} | {delta} |')
    report += ['', '所有数据只来自已通过数据、统计、模型执行及正常关机核验的新实测。', '',
               '未锁页与单轮结果不足以证明稳定性能收益。原始指标、操作样本数及长尾均在CSV；未测项目不补零。', '']
    report += [f'![{Path(name).stem}](图表/{name})' for name in created]
    (root/'结果分析.md').write_text('\n'.join(report)+'\n')
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    a = p.parse_args()
    print(render(a.root))
