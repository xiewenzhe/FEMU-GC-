"""仅对已归档且重复通过的抵扣校准实验绘制真实指标。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();proof=args.results/'抵扣校准核验.json';data=json.loads(proof.read_text())
    state=json.loads((args.results/'状态.json').read_text());stage=json.loads((args.results/'阶段归档.json').read_text())
    assert data['通过'] and stage['完整'] and stage['业务成功'] and state['当前客机PID'] is None
    assert not args.output.exists(),'不覆盖旧图'
    rows=[];labels=[]
    for case in ('retire','live'):
        for mode in ('greedy','model'):
            group=data['各组'][case+'-'+mode];assert group['重复']['通过']
            a,b=group['各轮'];assert a['维护']==b['维护']
            d=a['按文件复制'].get('/joint/D.sst',{});m=a['维护'];p=a['空间']['business']
            rows.append(dict(情形=case,选择器=mode,重复次数=2,ZC次数=m['ZC次数'],RR次数=m['RR次数'],
                ZC复制MiB=m['ZC迁移字节']/2**20,RR复制MiB=m['RR迁移字节']/2**20,
                总复制MiB=m['总迁移字节']/2**20,D复制MiB=(d.get('ZC字节',0)+d.get('RR字节',0))/2**20,
                空闲MiB=p['空闲']/2**20,有效MiB=p['有效']/2**20,满区可回收MiB=p['满区可回收']/2**20,
                Active=p['采样Active'],H终点最大RC=a['H终点最大RC'],实际改选次数=a['实际改选次数'],
                **a['业务设备增量']))
            labels.append(('D退役' if case=='retire' else 'D存活')+'\n'+('Greedy' if mode=='greedy' else '冻结模型'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties,findfont
    findfont(FontProperties(family='WenQuanYi Zen Hei'),fallback_to_default=False)
    plt.rcParams.update({'font.family':'WenQuanYi Zen Hei','pdf.fonttype':42,'axes.unicode_minus':False})
    fig,axes=plt.subplots(1,2,figsize=(10,4.2));xs=list(range(4))
    axes[0].bar(xs,[r['ZC复制MiB'] for r in rows],color='#3687aa',label='ZC复制')
    axes[0].bar(xs,[r['RR复制MiB'] for r in rows],bottom=[r['ZC复制MiB'] for r in rows],color='#e7a14b',label='RR复制')
    axes[1].bar(xs,[r['D复制MiB'] for r in rows],color=['#888888','#3687aa']*2)
    for index,r in enumerate(rows):
        axes[0].text(index,r['总复制MiB']+.12,f"{r['总复制MiB']:g}",ha='center')
        axes[1].text(index,r['D复制MiB']+.04,f"{r['D复制MiB']:g}",ha='center')
    axes[0].set_ylabel('全业务窗有效数据复制（MiB）');axes[0].legend(loc='upper center',ncol=2,frameon=False)
    axes[0].set_ylim(0,max(r['总复制MiB'] for r in rows)*1.25)
    axes[1].set_ylabel('冷文件D被复制的字节（MiB）');axes[1].set_ylim(0,max(r['D复制MiB'] for r in rows)*1.35)
    for ax in axes:ax.set_xticks(xs,labels);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('真正避免复制，还是推迟维护？',fontsize=14)
    fig.text(.5,.02,'受控ZenFS微型机制验证；含追加256MiB写入及1024热读；每组两次；不代表应用收益',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.09,1,.94));args.output.mkdir(parents=True)
    for suffix in ('pdf','png'):fig.savefig(args.output/('抵扣校准.'+suffix),dpi=180,bbox_inches='tight')
    plt.close(fig)
    with (args.output/'抵扣校准指标.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.output/'来源.json').write_text(json.dumps(dict(结果=str(proof.resolve()),
        结果SHA256=hashlib.sha256(proof.read_bytes()).hexdigest(),
        绘图源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),边界=data['边界']),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
