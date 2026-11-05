"""仅绘制已完成、已通过重复核验的三情形真实结果，不代填缺失数据。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--proof',type=Path,help='独立复核报告；原始结果及归档仍从results核验')
    args=parser.parse_args();source=args.proof if args.proof is not None else args.results/'三情形核验.json'
    data=json.loads(source.read_text());assert data['通过']
    state=json.loads((args.results/'状态.json').read_text())
    archive=json.loads((args.results/'阶段归档.json').read_text())
    assert state['当前客机PID'] is None and archive['完整'] and archive['业务成功']
    assert not args.output.exists(),'不覆盖已有图表'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties,findfont
    font=FontProperties(family='WenQuanYi Zen Hei')
    findfont(font,fallback_to_default=False)
    plt.rcParams.update({'font.family':'WenQuanYi Zen Hei','pdf.fonttype':42,'axes.unicode_minus':False})
    rows=[];figure,axes=plt.subplots(1,3,figsize=(10,4),sharey=True)
    for ax,(case,title) in zip(axes,(('w-first','W先失效'),('rr-first','RR先到'),('cooled','热点消退'))):
        proof=data['对照'][case]
        for index,key in enumerate(('Greedy普通放置','Greedy协同放置')):
            values=proof[key];zc=values['ZC迁移字节']/(1<<20);rr=values['RR迁移字节']/(1<<20)
            ax.bar(index,zc,color='#3687aa',label='ZC' if index==0 else None)
            ax.bar(index,rr,bottom=zc,color='#e7a14b',label='RR' if index==0 else None)
            ax.text(index,zc+rr+3,f'{zc+rr:g}',ha='center',fontsize=11)
            rows.append(dict(情形=title,放置=key,重复次数=2,ZC次数=values['ZC次数'],RR次数=values['RR次数'],
                ZC迁移MiB=zc,RR迁移MiB=rr,总迁移MiB=zc+rr))
        ax.set_xticks([0,1],['普通放置\nK=0','协同放置\nK=1'])
        ax.set_title(title);ax.set_ylim(0,190);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    axes[0].set_ylabel('业务期有效数据迁移量（MiB）')
    axes[0].legend(loc='upper left',frameon=False)
    figure.suptitle('相同Greedy下，协同放置的收益与代价',fontsize=14)
    figure.text(.5,.02,'受控ZenFS文件载荷；每组两次重复；有效量及可用空间终点一致；不代表YCSB性能',
                ha='center',fontsize=9)
    figure.tight_layout(rect=(0,.09,1,.94));args.output.mkdir(parents=True)
    figure.savefig(args.output/'三情形迁移量.pdf',bbox_inches='tight')
    figure.savefig(args.output/'三情形迁移量.png',dpi=180,bbox_inches='tight');plt.close(figure)
    with (args.output/'三情形指标.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.output/'来源.json').write_text(json.dumps(dict(结果=str(source.resolve()),
        结果SHA256=hashlib.sha256(source.read_bytes()).hexdigest(),
        绘图源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        说明=data['边界']),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
