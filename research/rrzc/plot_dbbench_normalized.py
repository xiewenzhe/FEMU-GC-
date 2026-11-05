"""按用户要求新增相对基线图；保留原始结果，零基线及缺失数据不造比值。"""
import argparse
import csv
import json
import math
from pathlib import Path

from plot_dbbench import NAMES
from ycsb_af import digest, require

MAINTENANCE = (
    ('总迁移字节', '总有效迁移量', '越低越好'),
    ('RR次数', 'RR完成次数', '越低越好'),
    ('ZC次数', 'ZC完成次数', '越低越好'),
    ('总成功ZoneReset', '成功Zone Reset', '越低越好'),
    ('推导物理Block擦除次数', '推导Block擦除', '越低越好'),
    ('业务进程吞吐ops每秒', '总业务吞吐', '越高越好'),
)
QUANTILES = ('平均微秒', 'P99微秒', 'P99.9微秒', 'P99.99微秒')


def ratio(value, baseline):
    """不反转吞吐方向；所有指标统一采用模型值/基线值。"""
    for item in (value, baseline):
        if item is not None:
            require(math.isfinite(item) and item >= 0, '指标存在负数或非有限值')
    if value is None or baseline is None or baseline == 0:
        return None
    return value/baseline


def metric_rows(pairs):
    rows = []
    for name, pair, _ in pairs:
        a, b = pair['模式']['space'], pair['模式']['model']
        metrics = [(key, title, direction, a[key], b[key]) for key, title, direction in MAINTENANCE]
        for op in ('READ', 'WRITE'):
            for key in QUANTILES:
                metrics.append((op+'-'+key, op+' '+key, '越低越好',
                    a['操作延迟'].get(op, {}).get(key), b['操作延迟'].get(op, {}).get(key)))
        for key, title, direction, baseline, model in metrics:
            value = ratio(model, baseline)
            state = '可归一化'
            if baseline is None or model is None:
                state = '缺少该操作统计，不适用'
            elif baseline == 0:
                state = '两组均为0，比例不适用' if model == 0 else '基线为0、模型非0，比例不适用'
            rows.append(dict(负载=NAMES[name], 负载标识=name, 指标=key, 指标名称=title,
                方向=direction, 基线原始值=baseline, 模型原始值=model,
                基线归一化=1.0 if value is not None else None, 模型归一化=value,
                相对变化百分比=(value-1)*100 if value is not None else None,
                原始差值=model-baseline if baseline is not None and model is not None else None,
                状态=state))
    return rows


def render(root, output_dir=None, *, baseline_description='当前基线为空间控制，非stock Greedy'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.backends.backend_pdf import PdfPages
    import numpy as np

    root = Path(root)
    pairs = []
    for name in NAMES:
        file = root/f'full-{name}-配对核验.json'
        if file.exists():
            pair = json.loads(file.read_text())
            require(pair['配对验收通过'], '未验收配对不能归一化')
            require(all(pair['模式'][m]['验收通过'] for m in ('space', 'model')), '单臂未验收')
            pairs.append((name, pair, file))
    require(pairs, '没有完成的正式配对，不生成示例图')
    rows = metric_rows(pairs)
    out = Path(output_dir) if output_dir is not None else root/'图表/归一化'
    out.mkdir(parents=True, exist_ok=True)
    with (out/'归一化指标.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    # TrueType中文字体配合Type 42导出，嵌入Unicode映射供PDF预览和复制使用。
    fonts = [f.name for f in font_manager.fontManager.ttflist if f.name == 'WenQuanYi Zen Hei']
    require(fonts, '缺少文泉驿正黑TrueType中文字体，不生成乱码图')
    plt.rcParams.update({'font.family':fonts[0], 'axes.unicode_minus':False,
                         'pdf.fonttype':42, 'svg.fonttype':'none'})
    indexed = {(r['负载标识'], r['指标']):r for r in rows}
    x = np.arange(len(pairs))
    def bars(ax, key, title, direction):
        records = [indexed[name, key] for name, _, _ in pairs]
        for mode, column, label, color, offset in (
            ('space', '基线归一化', '基线（空间对照）', '#4477AA', -.18),
            ('model', '模型归一化', '联合成本模型', '#EE7733', .18)):
            values = [r[column] for r in records]
            artists = ax.bar(x+offset, [float('nan') if v is None else v for v in values],
                .36, label=label, color=color)
            ax.bar_label(artists, labels=['' if v is None else f'{v:.3f}' for v in values], fontsize=8, padding=3)
        for i, row in enumerate(records):
            if row['模型归一化'] is None:
                note = '基线为0\n比例不适用' if row['基线原始值'] == 0 else '缺少统计\n不适用'
                ax.text(i, .04, note, transform=ax.get_xaxis_transform(), ha='center', fontsize=8)
        positive = [r[c] for r in records for c in ('基线归一化','模型归一化') if r[c] is not None]
        ax.set_ylim(0, max([1, *positive])*1.3)
        ax.axhline(1, color='#666666', linestyle='--', linewidth=.8)
        ax.set_xticks(x, [NAMES[k] for k, _, _ in pairs])
        ax.set_xlim(-.65, len(pairs)-.35)
        ax.set_ylabel('相对基线比值'); ax.set_title(title+'（'+direction+'）')
        ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    created = []
    with PdfPages(out/'全部归一化柱状图.pdf') as pdf:
        def save(fig, name):
            fig.suptitle(name+'\n模型值 ÷ 基线值；有效基线=1，未倒置吞吐量', fontsize=12)
            fig.tight_layout(rect=(0,.09,1,.89))
            fig.text(.02,.015, baseline_description+'；零基线/缺失不画比例。并行单轮结果不能证明模型因果收益。', fontsize=8)
            for suffix in ('png','svg'):
                fig.savefig(out/(name+'.'+suffix), dpi=220, bbox_inches='tight')
            pdf.savefig(fig, bbox_inches='tight'); plt.close(fig); created.append(name)
        fig, axes = plt.subplots(2, 3, figsize=(15,8))
        for ax, (key, title, direction) in zip(axes.flat, MAINTENANCE):
            bars(ax, key, title, direction)
        axes[0,0].legend(fontsize=8); save(fig,'维护与吞吐归一化')
        for op in ('READ','WRITE'):
            fig, axes = plt.subplots(2,2,figsize=(12,8))
            for ax, key in zip(axes.flat, QUANTILES):
                bars(ax, op+'-'+key, key.replace('微秒',''), '越低越好')
            axes[0,0].legend(fontsize=8); save(fig,op+'延迟归一化')
    source = dict(输入SHA256={str(p):digest(p) for _,_,p in pairs}, 绘图器SHA256=digest(Path(__file__)),
        公式='模型值/space基线值；正基线下基线=1；吞吐方向不反转',
        基线=baseline_description+'；没有补跑或替换基线',
        零基线='比例不适用，保留两组原始值及差值；不作0/0=1或引入epsilon',
        缺失='hot没有原生合并写直方图，留空不补零', 原始文件保留=True,
        图表=created, 限制='数据来自已验收真实配对；归一化不消除并行干扰或证明因果关系')
    (out/'归一化来源.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
    lines = ['# 相对基线归一化', '',
        '所有指标使用模型值÷本轮space基线值；有效基线为1。延迟、迁移和维护次数越低越好，吞吐越高越好。',
        baseline_description+'；这里只改结果展示，没有更换基线或重跑。',
        '基线为0或缺少原生统计时不计算比值；原始数字及差值保留在CSV中。', '',
        '|负载|指标|基线原值|模型原值|模型/基线|', '|---|---|---:|---:|---:|']
    for row in rows:
        if row['指标'] in {key for key,_,_ in MAINTENANCE}:
            value = '不适用' if row['模型归一化'] is None else f'{row["模型归一化"]:.4f}'
            lines.append(f'|{row["负载"]}|{row["指标名称"]}|{row["基线原始值"]}|{row["模型原始值"]}|{value}|')
    lines += ['', '原始图及CSV未改。单轮RR或延迟差异不能在没有实际ZC选区时归因于victim模型。', '']
    (out/'归一化说明.md').write_text('\n'.join(lines))
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output-dir', type=Path, help='另存图表，保留已有输出')
    args = parser.parse_args()
    print(render(args.root, args.output_dir))
