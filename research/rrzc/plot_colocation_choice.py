"""从已归档、重复通过的四组合结果绘图，不用示例数补齐未完成实验。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();source=args.results/'四组合核验.json'
    data=json.loads(source.read_text());assert data['通过']
    status=json.loads((args.results/'状态.json').read_text())
    archive=json.loads((args.results/'阶段归档.json').read_text())
    assert status['当前客机PID'] is None and archive['完整'] and archive['业务成功']
    assert not args.output.exists(),'不覆盖旧图'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties,findfont
    findfont(FontProperties(family='WenQuanYi Zen Hei'),fallback_to_default=False)
    plt.rcParams.update({'font.family':'WenQuanYi Zen Hei','pdf.fonttype':42,'axes.unicode_minus':False})
    rows=[];labels=[]
    for k in (0,1):
        for mode in ('greedy','model'):
            group=data['各组'][f'k{k}-{mode}'];assert group['重复']['通过']
            a,b=group['各轮'];assert a['维护']==b['维护']
            ledger=a['维护'];space=a['空间']['business']
            labels.append(('普通放置' if not k else '协同放置')+'\n'+('Greedy' if mode=='greedy' else '冻结模型'))
            rows.append(dict(K=k,选择器=mode,重复次数=2,ZC次数=ledger['ZC次数'],RR次数=ledger['RR次数'],
                ZC迁移MiB=ledger['ZC迁移字节']/(1<<20),RR迁移MiB=ledger['RR迁移字节']/(1<<20),
                总迁移MiB=ledger['总迁移字节']/(1<<20),改选次数=a['实际改选次数'],
                终点空闲MiB=space['空闲']/(1<<20),终点满区可回收MiB=space['满区可回收']/(1<<20),
                终点Active=space['采样Active']))
    fig,axes=plt.subplots(1,2,figsize=(11,4.3));xs=list(range(4))
    zc=[r['ZC迁移MiB'] for r in rows];rr=[r['RR迁移MiB'] for r in rows]
    axes[0].bar(xs,zc,color='#3687aa',label='ZC复制')
    axes[0].bar(xs,rr,bottom=zc,color='#e7a14b',label='RR复制')
    for x,r in enumerate(rows):axes[0].text(x,r['总迁移MiB']+3,f"{r['总迁移MiB']:g}",ha='center')
    axes[0].set_ylabel('总有效数据迁移量（MiB）');axes[0].set_ylim(0,max(r['总迁移MiB'] for r in rows)*1.2)
    axes[1].bar([x-.18 for x in xs],[r['ZC次数'] for r in rows],width=.36,color='#3687aa',label='ZC次数')
    axes[1].bar([x+.18 for x in xs],[r['RR次数'] for r in rows],width=.36,color='#e7a14b',label='RR次数')
    axes[1].set_ylabel('维护次数');axes[1].set_ylim(0,max(r['ZC次数']+r['RR次数'] for r in rows)+1)
    for ax in axes:
        ax.set_xticks(xs,labels);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.legend(frameon=False)
    fig.suptitle('分离协同放置与选区模型的贡献',fontsize=14)
    fig.text(.5,.02,'受控ZenFS文件；每组独立重复两次；原候选资格；空间及Active另见原始指标；不是YCSB性能',
        ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.09,1,.94));args.output.mkdir(parents=True)
    for suffix in ('pdf','png'):fig.savefig(args.output/('四组合维护量.'+suffix),dpi=180,bbox_inches='tight')
    plt.close(fig)
    with (args.output/'四组合指标.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.output/'来源.json').write_text(json.dumps(dict(结果=str(source.resolve()),
        结果SHA256=hashlib.sha256(source.read_bytes()).hexdigest(),
        绘图源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        边界=data['边界']),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
