"""从已归档的同一对实验生成原值与Greedy归一化图，不启动实验或修改原始数据。"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault('MPLCONFIGDIR', '/tmp/rrzc-figure-cache')
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt, font_manager
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load(source):
    """汇总文件与逐请求证据交叉核对；API百分位也采用最近秩。"""
    reports, inputs, api = {}, {}, {}
    for tag in ('p1', 'm1'):
        reports[tag] = {}
        for name in ('应用端到端核验', '核验', '业务收尾核验', '时间核验'):
            path = source / f'{tag}-{name}.json'
            data = json.loads(path.read_text())
            assert data['通过'], f'{tag}的{name}未通过'
            inputs[str(path)] = sha(path)
            reports[tag][name] = data
        r = reports[tag]
        app, base = r['应用端到端核验'], r['核验']
        assert (app['队列容量'], app['写批大小'], app['完成请求数'],
                app['实际同步写批次']) == (4096, 2048, 2260992, 720)
        assert base['维护']['ZC迁移字节'] + base['维护']['RR迁移字节'] == base['维护']['总迁移字节']
        assert base['维护']['复制日志与维护汇总相同']
        assert base['维护']['映射提交失败数'] == 0
        path = source / '逐臂采证' / tag / '请求提交.bin'
        with path.open('rb') as f:
            assert f.read(8) == b'RRZCREQ1'
        assert path.stat().st_size == 8 + 56 * app['完成请求数']
        inputs[str(path)] = sha(path)
        assert inputs[str(path)] == app['输入SHA256']['请求提交.bin']
        rows = np.memmap(path, dtype='<u8', mode='r', offset=8).reshape(-1, 7)
        api[tag] = {}
        for role, label, count in ((0, 'READ', 786432), (1, 'WRITE', 1474560)):
            values = np.sort(rows[rows[:, 1] == role, 5] - rows[rows[:, 1] == role, 4])
            assert len(values) == count
            api[tag][label] = {'平均': int(values.sum()) / count / 1000}
            for title, numerator in (('P99', 990000), ('P99.9', 999000), ('P99.99', 999900)):
                api[tag][label][title] = int(values[(count * numerator + 999999) // 1000000 - 1]) / 1000
        del rows
    path = source / '同对起点请求核验.json'
    paired = json.loads(path.read_text())
    assert paired['请求']['逻辑提交相同'] and paired['请求']['首差'] is None
    for k, v in paired['起点'].items():
        if isinstance(v, dict) and '相同' in v and k != '设备会话及Reset代际原值':
            assert v['相同'], f'起点不同：{k}'
    inputs[str(path)] = sha(path)
    return reports, api, inputs


def collect(reports, api):
    metrics = []

    def add(group, title, unit, direction, fn):
        values = [float(fn(tag)) for tag in ('p1', 'm1')]
        assert all(math.isfinite(v) and v >= 0 for v in values)
        assert values[0] > 0, '零基线应单独标注，不能制造比值'
        ratio = values[1] / values[0]
        metrics.append(dict(分组=group, 指标=title, 单位=unit, 优化方向=direction,
            普通放置Greedy=values[0], 协同放置Greedy=values[1],
            协同除普通=ratio, 相对变化百分比=(ratio - 1) * 100))

    app = lambda t: reports[t]['应用端到端核验']
    base = lambda t: reports[t]['核验']
    add('总体结果', 'ZC＋RR总有效迁移量', 'MiB', '降低', lambda t: base(t)['维护']['总迁移字节'] / (1 << 20))
    add('总体结果', '应用完成吞吐量', 'ops/s', '提高', lambda t: app(t)['应用完成吞吐ops'])
    for role, title in (('READ', '读'), ('WRITE', '写')):
        index = 0 if role == 'READ' else 1
        add('总体结果', f'应用平均{title}完成延迟', 'ms', '降低', lambda t, i=index: app(t)['指标'][i]['平均毫秒'])
    for key in ('RR次数', 'ZC次数'):
        add('总体结果', key, '次', '降低', lambda t, k=key: base(t)['维护'][k])
    for role, title in ((0, '读'), (1, '写')):
        for key, label in (('P99毫秒', 'P99'), ('P999毫秒', 'P99.9'), ('P9999毫秒', 'P99.99')):
            add('应用长尾延迟', f'应用{title}完成延迟 {label}', 'ms', '降低',
                lambda t, i=role, k=key: app(t)['指标'][i][k])
    for key, title in (('ZC迁移字节', 'ZC有效迁移量'), ('RR迁移字节', 'RR有效迁移量')):
        add('维护与空间', title, 'MiB', '降低', lambda t, k=key: base(t)['维护'][k] / (1 << 20))
    add('维护与空间', '成功Zone Reset', '次', '降低', lambda t: base(t)['维护']['全部成功Reset次数'])
    add('维护与空间', '普通分配Finish', '次', '降低', lambda t: base(t)['资源']['普通成功Finish次数'])
    add('维护与空间', '普通Finish累计尾部损失', 'MiB', '降低', lambda t: base(t)['资源']['普通Finish尾损字节'] / (1 << 20))
    add('维护与空间', '末端可用空间', 'MiB', '提高', lambda t: base(t)['末端空间']['空闲字节'] / (1 << 20))
    for role, title in (('READ', 'Get读'), ('WRITE', 'Put写')):
        for key in ('平均', 'P99', 'P99.9', 'P99.99'):
            add('API响应延迟', f'{title}API {key}', 'µs', '降低', lambda t, r=role, k=key: api[t][r][k])
    add('补充时间', '应用总完成时间', 's', '降低', lambda t: app(t)['应用产生至最终完成秒'])
    add('补充时间', '同步Flush调用累计', 's', '降低', lambda t: reports[t]['业务收尾核验']['请求窗口']['显式Flush秒'])
    return metrics


def render(source, output, audit_scripts):
    sys.path.insert(0, str(audit_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    assert not output.exists(), '不覆盖已有图表，请使用新的输出目录'
    reports, api, inputs = load(source)
    metrics = collect(reports, api)
    output.mkdir(parents=True)
    qa = output / '核验'
    qa.mkdir()
    assert any(f.name == 'WenQuanYi Zen Hei' for f in font_manager.fontManager.ttflist), '缺少中文字体'
    plt.rcParams.update({'font.family': 'sans-serif',
        'font.sans-serif': ['WenQuanYi Zen Hei', 'DejaVu Sans'], 'font.size': 11,
        'axes.titlesize': 12, 'axes.labelsize': 10, 'xtick.labelsize': 10,
        'ytick.labelsize': 10, 'axes.unicode_minus': False, 'pdf.fonttype': 42,
        'svg.fonttype': 'none', 'axes.spines.top': False, 'axes.spines.right': False})
    for normalized, dirname in ((False, '原始数值'), (True, '归一化')):
        dest = output / dirname
        dest.mkdir()
        with PdfPages(dest / '全部柱状图.pdf') as combined:
            for group in ('总体结果', '应用长尾延迟', '维护与空间', 'API响应延迟'):
                rows = [m for m in metrics if m['分组'] == group]
                cols = 4 if len(rows) == 8 else 3
                fig, axes = plt.subplots(2, cols, figsize=(16 if cols == 4 else 13.5, 8.3))
                fig.subplots_adjust(left=.075, right=.97, bottom=.16, top=.82, wspace=.42, hspace=.70)
                mode = '协同放置 / 普通放置Greedy' if normalized else '原始实测值'
                fig.suptitle(f'{group}｜{mode}', fontsize=18, y=.965)
                fig.text(.5, .91, '普通放置K=0＋Greedy  vs  协同放置K=1＋Greedy；每组一次真实运行', ha='center', fontsize=11)
                for ax, m in zip(axes.flat, rows):
                    values = [1, m['协同除普通']] if normalized else [m['普通放置Greedy'], m['协同放置Greedy']]
                    bars = ax.bar([0, 1], values, width=.52, color=['#8393A8', '#27858A'], zorder=3)
                    for bar in bars:
                        bar.set_edgecolor('white')
                    arrow = '↑' if m['优化方向'] == '提高' else '↓'
                    ax.set_title(m['指标'] + ' ' + arrow, pad=14)
                    ax.set_xticks([0, 1], ['普通放置', '协同放置'])
                    ax.set_ylabel('相对值（普通Greedy＝1）' if normalized else m['单位'])
                    ax.set_xlim(-.65, 1.65)
                    ax.set_ylim(0, max(values) * 1.29)
                    ax.yaxis.set_major_locator(plt.MaxNLocator(4))
                    ax.ticklabel_format(axis='y', style='plain', useOffset=False)
                    # 两臂的值直接标注，便于汇报时独立阅读每一幅图。
                    labels = [f'{v:.3f}' if normalized else (f'{int(v)}' if m['单位'] == '次' else f'{v:,.2f}') for v in values]
                    ax.bar_label(bars, labels=labels, padding=10, fontsize=11)
                fig.text(.075, .075, '8 GiB；Zone 128 MiB；Open/Active＝8/12；RR＝1024；GC＝20%；缓存和WAL关闭。', fontsize=10)
                note = ('应用延迟包含排队；队列4096、每2048写同步Flush确认。API写延迟仅为Put返回，不代表落盘完成。'
                    if group == 'API响应延迟' else '应用延迟包含排队与批确认；队列4096、每2048写同步Flush。↑越高越好，↓越低越好。')
                fig.text(.075, .043, note, fontsize=10)
                fig.text(.075, .015, '单对受控实验，无独立重复误差条；Reset是Zone命令计数，不作为实测物理Block擦除次数。', fontsize=9)
                fig.canvas.draw()
                require_matplotlib_panel_alignment(fig, json_out=qa / f'{dirname}-{group}-对齐.json', strict=True)
                fig.savefig(dest / (group + '.png'), dpi=300)
                fig.savefig(dest / (group + '.svg'))
                fig.savefig(dest / (group + '.pdf'))
                combined.savefig(fig)
                plt.close(fig)
    with (output / '原始与归一化指标.csv').open('x', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    notes = ['# 本轮端到端实验图表', '',
        '本图汇总最近完成的有界应用队列实验，而非编辑器仍打开的旧首区模型实验。两臂均使用Greedy，比较普通放置K0与协同放置K1，不能归因于cost-benefit选区模型。', '',
        '结论：维护复制与空间压力降低，应用排队和批确认缩短；Get平均API时间基本不变，RR次数反而增加。没有排除不利指标。', '',
        '原始数值和归一化各四页：总体结果、应用长尾延迟、维护与空间、API响应延迟。每页有PNG、SVG及PDF，另附合并PDF。图为定量对照网格，面向组会汇报；画布13.5或16英寸宽，所有文字至少9pt。', '',
        '归一化公式为协同放置值÷普通放置Greedy值。所有指标保留原方向：吞吐、可用空间越高越好；延迟、迁移、维护次数越低越好。Reset、Finish次数作为维护成本指标，不能单独解释成性能好坏。无零基线。', '',
        '每臂786432读、1474560写、720次同步写批确认，共2260992请求；两臂逻辑提交一致且起点已采集状态通过规范化核验。设备会话及Reset绝对代际不相同，不声称完整NAND检查点一致。', '',
        '应用延迟：产生→入队背压→FIFO等待→API→最终确认；读在Get成功并校验后返回，写在所属2048写批同步Flush成功后确认。单生产者＋单消费者、队列4096，无QPS限速，到达时间受背压影响，不是相同外部时间戳回放。该写完成是当前模拟设备语义，不证明掉电持久性。', '',
        'API延迟从本轮原始请求记录重新计算，为调用开始至返回，不含调用前排队与显式Flush。Put在关闭WAL时不等于写落盘。应用与API长尾均使用最近秩P99/P99.9/P99.99。SSD单次命令响应分布本轮未单独采集，不混入上一轮约45微秒的命令数据。', '',
        '迁移统计覆盖业务及收尾的成功复制；ZC/RR两类相加严格等于总迁移。普通Finish只统计普通分配路径，非所有原因Finish；累计尾损是生命周期累计值，不是末端同时损失。最终有效数据两臂相同。', '',
        '每组仅一次，不计算显著性或编造误差条。图反映当前受控配置，不能外推正常多客户端并发或标准YCSB。', '',
        '|指标|普通放置Greedy|协同放置Greedy|单位|相对变化|', '|---|---:|---:|---|---:|']
    for m in metrics:
        notes.append(f"|{m['指标']}|{m['普通放置Greedy']:.6f}|{m['协同放置Greedy']:.6f}|{m['单位']}|{m['相对变化百分比']:+.3f}%|")
    (output / '结果汇总与图注.md').write_text('\n'.join(notes) + '\n')
    manifest = dict(原始来源=str(source), 输入SHA256=inputs, 绘图源码SHA256=sha(Path(__file__)),
        归一化公式='协同放置Greedy / 普通放置Greedy', 独立配对数=1,
        API百分位='原始请求纳秒按最近秩重算', 图中误差条='无：只有一对实测',
        修改实验行为=False, 图表后端='Python / matplotlib',
        文件SHA256={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()})
    (output / '来源与文件核验.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(输出=str(output), 指标数量=len(metrics), 输入核验='通过'), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--audit-scripts', type=Path, required=True)
    args = parser.parse_args()
    render(args.source, args.output, args.audit_scripts)
