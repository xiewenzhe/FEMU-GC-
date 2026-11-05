"""只绘制已完成并重复核验的真实SST存活轨迹；横轴阶段不等长。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();proof=args.results/'生命周期机会核验.json';data=json.loads(proof.read_text())
    state=json.loads((args.results/'状态.json').read_text());archive=json.loads((args.results/'阶段归档.json').read_text())
    assert data['通过'] and data['重复']['通过'] and archive['完整'] and archive['业务成功'] and state['当前客机PID'] is None
    assert not args.output.exists(),'不覆盖图表'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties,findfont
    findfont(FontProperties(family='WenQuanYi Zen Hei'),fallback_to_default=False)
    plt.rcParams.update({'font.family':'WenQuanYi Zen Hei','pdf.fonttype':42,'axes.unicode_minus':False})
    fig,axes=plt.subplots(2,1,figsize=(9,6),sharex=True);rows=[]
    for tag,style,color in [('p1','-','#3687aa'),('p2','--','#e7a14b')]:
        layouts=data['各臂'][tag]['阶段布局'];xs=[r['布局'] for r in layouts]
        axes[0].step(xs,[r['原W存活'] for r in layouts],where='post',linestyle=style,color=color,label=tag+'：全部原W SST')
        retired=[r['原W失效按键窗口'].get('0',0) for r in layouts]
        axes[1].step(xs,[30-n for n in retired],where='post',linestyle=style,color=color,label=tag+'：窗口0原W SST')
        for r in layouts:
            rows.append(dict(实验臂=tag,阶段=r['布局'],原H存活=r['原H存活'],原W存活=r['原W存活'],
                窗口0原W存活=30-r['原W失效按键窗口'].get('0',0),新失效W窗口='/'.join(map(str,r['新失效W所属窗口']))))
    initial_w=max(a['阶段布局'][0]['原W存活'] for a in data['各臂'].values())
    axes[0].set_ylabel('原标签W SST存活数量');axes[0].set_ylim(0,max(1,initial_w)*1.06)
    axes[1].set_ylabel('窗口0原W SST存活数量');axes[1].set_ylim(0,33)
    fixed_h=data['各臂']['p1'].get('固定H',False)
    assert fixed_h==data['各臂']['p2'].get('固定H',False),'两个重复的读模式不同'
    for window in range(6 if fixed_h else 1):
        axes[1].axvspan(window*10+3,window*10+10,color='#999999',alpha=.2,
            label='H0实际读取批次' if window==0 else None)
    first=next((r['布局'] for r in data['各臂']['p1']['阶段布局'] if r['新失效W']),None)
    if first is not None:
        for ax in axes:ax.axvline(first,color='#333333',linestyle=':',alpha=.7)
        axes[1].text(first-1,18,'首次原W失效\n阶段'+str(first),ha='right',fontsize=10)
    for ax in axes:ax.grid(axis='y',alpha=.2);ax.legend(loc='lower left',frameon=False,ncol=2)
    axes[1].set_xlabel('负载阶段序号（各阶段时长不同，0为业务起点，61为末端快照）')
    axes[1].set_xlim(0,61)
    fig.suptitle(('固定H0跨窗口读取：' if fixed_h else '')+'原W SST何时真正失效？',fontsize=14)
    fig.tight_layout(rect=(0,0,1,.95));args.output.mkdir(parents=True)
    for suffix in ('pdf','png'):fig.savefig(args.output/('SST存活与阶段.'+suffix),dpi=180,bbox_inches='tight')
    plt.close(fig)
    with (args.output/'逐阶段SST存活.csv').open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (args.output/'来源.json').write_text(json.dumps(dict(结果=str(proof),结果SHA256=hashlib.sha256(proof.read_bytes()).hexdigest(),
        绘图源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        边界='真实定制db_bench的Greedy重复；SST存活按实际元数据与extent核验，不代表模型收益'),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
