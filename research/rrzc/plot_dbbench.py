"""仅绘制独立验收通过的db_bench配对；原生缺失写直方图不补零。"""
import csv
import json
from pathlib import Path
from ycsb_af import digest, require

LABELS = {'space':'空间对照', 'model':'去边界联合成本模型'}
NAMES = {'hot':'偏斜读＋并发写', 'mixed95':'均匀95读/5写', 'mixed50':'均匀50读/50写'}


def render(root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib import font_manager
    import numpy as np
    root = Path(root)
    pairs = [(k,json.loads((root/f'full-{k}-配对核验.json').read_text())) for k in NAMES
             if (root/f'full-{k}-配对核验.json').exists()]
    require(pairs and all(p['配对验收通过'] for _,p in pairs), '没有完整验收结果，不画示例图')
    fonts = [f.name for f in font_manager.fontManager.ttflist if 'Noto Sans CJK' in f.name]
    require(fonts, '缺少中文字体')
    plt.rcParams.update({'font.family':fonts[0], 'axes.unicode_minus':False})
    out = root/'图表'; out.mkdir(exist_ok=True)
    keys = ('RR次数','ZC次数','总迁移字节','RR迁移字节','ZC迁移字节','总成功ZoneReset',
            '推导物理Block擦除次数','业务进程吞吐ops每秒','实际读次数','实际写次数','正RR估计数','RR独立改选次数')
    with (out/'原始指标.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f,fieldnames=['负载','策略',*keys]); writer.writeheader()
        for name,pair in pairs:
            for mode,row in pair['模式'].items():
                writer.writerow(dict(负载=NAMES[name],策略=LABELS[mode],**{k:row[k] for k in keys}))
    with (out/'操作延迟.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f,fieldnames=['负载','策略','操作','次数','平均微秒','P99微秒','P99.9微秒','P99.99微秒'])
        writer.writeheader()
        for name,pair in pairs:
            for mode,row in pair['模式'].items():
                for op,values in row['操作延迟'].items():
                    writer.writerow(dict(负载=NAMES[name],策略=LABELS[mode],操作=op,**values))
    x = np.arange(len(pairs))
    def bars(ax, getter, title, unit):
        for i,mode in enumerate(LABELS):
            values = [getter(p['模式'][mode]) for _,p in pairs]
            art = ax.bar(x+(i-0.5)*.36,[float('nan') if v is None else v for v in values],.36,label=LABELS[mode])
            ax.bar_label(art,labels=['不适用' if v is None else f'{v:.2f}' for v in values],fontsize=7,padding=2)
        ax.set_xticks(x,[NAMES[k] for k,_ in pairs]); ax.set_title(title); ax.set_ylabel(unit)
        ax.set_ylim(bottom=0); ax.margins(y=.3); ax.grid(axis='y',alpha=.2)
    with PdfPages(out/'全部柱状图.pdf') as pdf:
        def save(fig,name):
            fig.suptitle(name+'\n8GiB / RR1024 / GC20% / 并行单轮，未锁页、不限速',fontsize=12)
            fig.tight_layout(rect=(0,.07,1,.88))
            fig.text(.02,.01,'共享宿主有干扰；操作直方图非裸IO；擦除为Reset×128推导。hot原生无合并写直方图。',fontsize=8)
            for ext in ('png','svg'):fig.savefig(out/(name+'.'+ext),dpi=200,bbox_inches='tight')
            pdf.savefig(fig,bbox_inches='tight'); plt.close(fig)
        fig,axes=plt.subplots(2,3,figsize=(14,8))
        for ax,(key,title,scale,unit) in zip(axes.flat,[('总迁移字节','总有效迁移',2**20,'MiB'),
            ('RR次数','RR完成次数',1,'次'),('ZC次数','ZC完成次数',1,'次'),
            ('总成功ZoneReset','成功Zone Reset',1,'次'),('推导物理Block擦除次数','推导Block擦除',1,'次'),
            ('业务进程吞吐ops每秒','总业务操作/Run进程墙钟',1,'操作/秒')]):
            bars(ax,lambda r,k=key,s=scale:r[k]/s,title,unit)
        axes[0,0].legend(fontsize=8); save(fig,'维护与吞吐')
        for op in ('READ','WRITE'):
            fig,axes=plt.subplots(2,2,figsize=(12,8))
            for ax,key in zip(axes.flat,('平均微秒','P99微秒','P99.9微秒','P99.99微秒')):
                bars(ax,lambda r,k=key:r['操作延迟'].get(op,{}).get(k),key,'微秒')
            axes[0,0].legend(fontsize=8); save(fig,op+'操作延迟')
    inputs = {str(root/f'full-{k}-配对核验.json'):digest(root/f'full-{k}-配对核验.json') for k,_ in pairs}
    (out/'图表来源.json').write_text(json.dumps(dict(输入SHA256=inputs,绘图器SHA256=digest(Path(__file__)),
        限制='仅完整配对，不筛选正收益；直方图近似分位，非裸IO；并行结果需串行或重复验证'),ensure_ascii=False,indent=2))
    lines=['# db_bench实测结果','', '8GiB，RR1024，GC20%，并行单轮；空间对照不是stock Greedy。','',
        '|负载|RR 空间/模型|ZC 空间/模型|迁移MiB 空间/模型|RR驱动改选|', '|---|---:|---:|---:|---:|']
    for name,p in pairs:
        a,b=p['模式']['space'],p['模式']['model']
        lines.append(f'|{NAMES[name]}|{a["RR次数"]}/{b["RR次数"]}|{a["ZC次数"]}/{b["ZC次数"]}|'
            f'{a["总迁移字节"]/2**20:.3f}/{b["总迁移字节"]/2**20:.3f}|{b["RR独立改选次数"]}|')
    lines += ['', '总迁移量和实际改选需同时检查；单轮差异不直接作因果或稳态结论。',
        '装载和Run为两个原生进程，NAND/RC连续；验证为全键计数及块CRC，不是逐请求最新值审计。',
        'hot写线程原生不参与合并直方图；写延迟留空，不编造。总业务吞吐包含Run进程启动与关闭。','']
    (root/'结果分析.md').write_text('\n'.join(lines))
