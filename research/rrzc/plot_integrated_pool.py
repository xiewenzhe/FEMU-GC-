"""绘制完整业务对照；保留每次重复，不把两次工程重复冒充统计显著性。"""
import argparse
import csv
import json
from pathlib import Path

from ycsb_af import digest, require


def render(source, output, mechanism_label='在线协同与模型', scope_note=None):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt, font_manager
    from matplotlib.backends.backend_pdf import PdfPages

    report=json.loads(source.read_text())
    require(report['通过'] and report['重复']['Greedy']['通过'],'完整对照或基线重复未通过')
    require(not output.exists(),'不覆盖已有图表')
    fonts=[f.name for f in font_manager.fontManager.ttflist if f.name=='WenQuanYi Zen Hei']
    require(fonts,'缺少中文字体')
    plt.rcParams.update({'font.family':fonts[0],'axes.unicode_minus':False,'pdf.fonttype':42})
    arms=report['各臂'];rows=[]
    metrics=[
        ('维护','总迁移字节','总有效迁移量（MiB）',1/(1<<20)),
        ('维护','RR次数','RR次数',1),('维护','ZC次数','ZC次数',1),
        ('维护','全部成功Reset次数','成功Zone Reset次数',1),
        ('资源','普通Finish尾损字节','普通Finish尾损（MiB）',1/(1<<20)),
        ('计时','请求窗口吞吐ops','请求窗口吞吐（ops/s）',1),
    ]
    pages=[('维护与吞吐',metrics)]
    for role,title in (('READ','读API延迟'),('WRITE','写API延迟')):
        pages.append((title,[(role,k,title.replace('API延迟','')+' '+name+'（微秒）',.001)
            for k,name in (('平均纳秒','平均'),('P99纳秒','P99'),('P999纳秒','P99.9'),('P9999纳秒','P99.99'))]))
    output.mkdir(parents=True)
    with PdfPages(output/'全部归一化柱状图.pdf') as pdf:
        for title,metrics in pages:
            fig,axes=plt.subplots(2,3 if len(metrics)==6 else 2,figsize=(13,8))
            for ax,(group,key,label,scale) in zip(axes.flat,metrics):
                pairs=[]
                for n in (1,2):
                    values=[]
                    for tag in ('p'+str(n),'m'+str(n)):
                        r=arms[tag]
                        data=(next(x for x in r['API延迟'] if x['操作']==group)
                              if group in ('READ','WRITE') else r[group])
                        values.append(data[key]*scale)
                    baseline,model=values
                    ratio=model/baseline if baseline else None
                    pairs.append(ratio)
                    rows.append(dict(重复=n,指标=label,Greedy原值=baseline,机制原值=model,
                        机制除Greedy=ratio,相对变化百分比=(ratio-1)*100 if ratio is not None else None))
                    ax.bar(n-.17,1 if ratio is not None else float('nan'),.34,color='#4477AA',label='Greedy' if n==1 else None)
                    bars=ax.bar(n+.17,ratio if ratio is not None else float('nan'),.34,color='#EE7733',label=mechanism_label if n==1 else None)
                    if ratio is None:
                        ax.text(n,.05,f'基线为0\n原值 {baseline:g}/{model:g}',ha='center',fontsize=8)
                    else:ax.bar_label(bars,labels=[f'{ratio:.3f}'],padding=3,fontsize=9)
                ax.set_title(label);ax.set_xticks([1,2],['重复1','重复2'])
                ax.set_ylabel('机制值 / Greedy值');ax.axhline(1,color='gray',ls='--',lw=.7)
                ax.set_ylim(0,max([1,*[x for x in pairs if x is not None]])*1.3)
                ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
            axes.flat[0].legend(fontsize=8)
            fig.suptitle(title+'：总复制与延迟越低越好，吞吐越高越好')
            fig.tight_layout(rect=(0,.07 if scope_note else .05,1,.95))
            note='真实RocksDB受控db_bench；两次工程重复，不是独立种子的显著性检验；Reset非直接实测Block擦除。'
            if scope_note:note+='\n'+scope_note
            fig.text(.02,.012,note,fontsize=8,va='bottom')
            fig.savefig(output/(title+'.png'),dpi=180)
            pdf.savefig(fig);plt.close(fig)
    with (output/'归一化指标.csv').open('x',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,list(rows[0]));writer.writeheader();writer.writerows(rows)
    (output/'归一化来源.json').write_text(json.dumps(dict(来源=str(source),输入SHA256=digest(source),
        绘图器SHA256=digest(Path(__file__)),公式='每次机制值除以对应Greedy值；零基线不造比值',
        机制重复完全相同=report['重复']['机制']['通过'],物理Block擦除='未直接采集，不作实测图'),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();render(args.source,args.output)
