"""从已归档维护日志绘制真实空间—复制路径，不插值生成实验结果。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_joint_value import parse
from analyze_space_goal import arm,GOAL


def render(source,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    source,output=Path(source),Path(output)
    assert not output.exists(),'不覆盖已有图表'
    fonts=[f.name for f in font_manager.fontManager.ttflist if f.name=='WenQuanYi Zen Hei']
    assert fonts,'缺少可嵌入中文TrueType字体'
    plt.rcParams.update({'font.family':fonts[0],'axes.unicode_minus':False,'pdf.fonttype':42})
    records=[];provenance={}
    fig,ax=plt.subplots(figsize=(9,5.5))
    for tag,name,color in [('g1','Greedy','#4477AA'),('g2','既有V1模型','#EE7733')]:
        root=source/tag;data=arm(root)
        log=root/'goal.zenfs.log';tasks=parse(log.read_text().splitlines())['任务']
        assert (len(tasks)==len(data['收尾采样'])-1 and all(t['类型']=='ZC' for t in tasks)), \
            '本图仅适用于已核验的每次检查一次ZC且无RR的收尾轨迹'
        copied=data['业务']['总迁移字节'];rows=[]
        for point in data['收尾采样']:
            sequence=point['序号']
            if sequence:copied+=tasks[sequence-1]['实际复制']
            row=dict(策略=name,收尾检查序号=sequence,空闲字节=point['空闲字节'],
                业务加收尾迁移字节=copied,达到共同下限=point['达标'])
            rows.append(row);records.append(row)
        assert copied==data['业务加收尾迁移字节']
        x=[r['空闲字节']/2**20 for r in rows]
        y=[r['业务加收尾迁移字节']/2**20 for r in rows]
        ax.plot(x,y,'o-',color=color,label=name,linewidth=1.7,markersize=5)
        ax.annotate(f'{name}结束\n{x[-1]:.2f} MiB / {y[-1]:.2f} MiB',
            (x[-1],y[-1]),xytext=(875,1438) if tag=='g2' else (700,1427),
            textcoords='data',fontsize=9,color=color,va='top',
            arrowprops={'arrowstyle':'->','color':color})
        if tag=='g2':
            ax.annotate(f'第5次：{x[-2]:.2f} MiB\n尚未达到1 GiB',
                (x[-2],y[-2]),xytext=(-133,-6),textcoords='offset points',fontsize=9,
                arrowprops={'arrowstyle':'->','color':color},color=color)
        provenance[tag]={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root/'run.zenfs.log',log,root/'goal.log',root/'space.goal.json')}
    ax.axvline(GOAL/2**20,color='#666666',linestyle='--',label='共同最低目标：1 GiB')
    ax.set(xlabel='实际可用空间（MiB）',ylabel='累计有效数据迁移（业务＋收尾，MiB）',
           title='补足空间后，业务期少搬迁是否仍成立？')
    ax.grid(alpha=.2);ax.legend(loc='upper left')
    fig.text(.5,.01,'每个点均来自真实采样；连线仅辅助阅读。收尾两组共同Greedy，无前台请求。',
             ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.04,1,1))
    output.mkdir(parents=True)
    with (output/'空间与迁移路径.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    for suffix in ('png','pdf'):fig.savefig(output/('空间与迁移路径.'+suffix),dpi=160)
    plt.close(fig)
    (output/'来源.json').write_text(json.dumps(dict(来源日志SHA256=provenance,
        脚本SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        边界='实际执行路径，非连续成本曲线；原始业务与收尾分别核验，不预测未执行的Zone。'),
        ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();render(args.source,args.output)
