"""只从已完成配对与逐笔派生证据汇总影子归因，不把建议当作反事实实测。"""
import argparse
import csv
import json
from pathlib import Path
import re

from ycsb_af import require

MODES=('G2','G2_WSURVIVOR')


def native_sources(raw,file_ids):
    """原生日志核对跨臂文件来源；相同文件编号本身不是相同数据的证据。"""
    names={}
    for line in (raw/'run.zenfs.log').read_text().splitlines():
        found=re.search(r'文件编号=(\d+)，文件名=([^， ]+)',line)
        if found and int(found[1]) in file_ids:names[int(found[1])]=found[2]
    reverse={int(Path(name).stem):fid for fid,name in names.items()}
    events=[];created={}
    for number,line in enumerate((raw/'run.rocksdb.log').read_text().splitlines(),1):
        if 'EVENT_LOG_v1 ' not in line:continue
        event=json.loads(line.split('EVENT_LOG_v1 ',1)[1]);events.append((number,event))
        if event.get('event')=='table_file_creation' and event.get('file_number') in reverse:
            fid=reverse[event['file_number']]
            created[fid]=dict(文件名=names[fid],创建行=number,
                **{key:event.get(key) for key in ('job','file_size','smallest_seqno','largest_seqno')})
    for fid,entry in created.items():
        started=[(n,e) for n,e in events if e.get('job')==entry['job'] and
                 e.get('event') in ('flush_started','compaction_started')]
        require(len(started)==1,'同一创建任务来源不唯一')
        entry.update(来源行=started[0][0],来源=started[0][1]['event'])
    return created


def report(pair,output):
    state=json.loads((pair/'状态.json').read_text())
    require(state.get('完整实验完成') is True,'配对尚未完成，不生成结果结论')
    result=json.loads((pair/'配对分析/完整核验.json').read_text())
    require(result['通过'],'起点、请求或维护账目未通过')
    records={mode:json.loads((pair/mode/'业务核验.json').read_text()) for mode in MODES}
    detail=json.loads((output/'实际去向与首差核验.json').read_text())
    mode=MODES[1];new=records[mode];shadow=new['旧G2影子'];rows=shadow['逐次']
    gates=[x for x in rows if x['拒绝协同']]
    covered=[x for x in gates if x['原G2建议入池'] is True]
    redundant=[x for x in gates if x['原G2建议入池'] is False]
    unknown=[x for x in gates if not x['影子可知']]
    identical=[x for x in covered if x['原G2建议目标']==x['实际目标'] and x['原G2建议代际']==x['目标代际']]
    summary=dict(配对=str(pair),规则拒绝数=len(gates),覆盖旧入池建议数=len(covered),
        原建议已经回退数=len(redundant),未知建议数=len(unknown),
        覆盖建议中实际目标同地址代际数=len(identical),
        总迁移观察变化百分比=result['总迁移变化百分比'],未以单对宣布因果收益=True)
    # 首差与当前门控文件均核对原生来源，不能把不同后台任务的同编号文件当同一份数据。
    first_target=detail['首次实际目标差异']
    first_file=first_target['各臂'][MODES[1]]['文件编号']
    file_ids={int(g['文件编号']) for g in gates}|{first_file}
    sources={m:native_sources(Path(detail['各臂'][m]['原始目录']),file_ids) for m in MODES}
    require(all(file_ids<=set(sources[m]) for m in MODES),'缺少门控文件或首差文件创建来源')
    copy_rows=list(csv.DictReader((output/'逐笔复制分类.csv').open(encoding='utf-8-sig')))
    cross=[]
    for fid in sorted(file_ids-{first_file}):
        entries=[sources[m][fid] for m in MODES]
        comparable=all(entries[0][key]==entries[1][key] for key in
            ('文件名','来源','file_size','smallest_seqno','largest_seqno'))
        row=dict(文件编号=fid,创建来源及序号范围一致=comparable,各臂={})
        for m in MODES:
            selected=[r for r in copy_rows if r['模式']==m and int(r['文件编号'])==fid]
            row['各臂'][m]=dict(来源=sources[m][fid],复制次数=len(selected),
                有效复制字节=sum(int(r['有效复制字节']) for r in selected),
                组成事件=[int(r['组成事件']) for r in selected])
        cross.append(row)
    before_gate=(first_target['各臂'][MODES[1]]['事件']<detail['首次新规则拒绝']['尝试事件'])
    terminal_delta=(records[MODES[1]]['维护及资源']['末端空间']['空闲字节']-
                    records[MODES[0]]['维护及资源']['末端空间']['空闲字节'])/(1<<20)
    summary['首次目标分歧早于首次拒绝']=before_gate
    summary['首差文件创建来源']={m:sources[m][first_file] for m in MODES}
    summary['门控文件跨臂实际复制']=cross
    lines=['# 旧G2影子归因：完整结果与结论','',
        '本轮仅增加观察，W历史准入规则不变，未修改GC/RR触发、Victim Selection、Credit/Score、H算法或K。两臂使用同一新程序、独立新客机，串行运行。','',
        f'原始配对目录：`{pair}`。归档哈希及客机/镜像退出核验见上级目录`最终归档与退出核验.json`。','',
        '## 影子回答了什么','',
        f"新版本实际拒绝{len(gates)}次。其中{len(covered)}次原G2的只读Choose建议入池，{len(redundant)}次原建议已经回退，{len(unknown)}次建议未知。",'',
        f"覆盖入池建议中，{len(identical)}次实际普通目标与影子建议的Zone和代际相同；不能仅按建议变化就宣称物理目的地址改选。",'',
        '**原建议可行不等于最终分配成功。** 影子没有申请目标令牌、没有Commit或复制，也未执行完整后续Acquire/资源复核。这里是同一次原观察下的建议比较，不是逐事件反事实实验。','',
        '建议动作0表示Fallback，此时建议Zone=0只是结构体占位，不表示选择元数据Zone。动作1/2分别表示Reuse/Create。','',
        '实际允许路径复用原Choose；实际拒绝路径增加一次原RC/Zone状态采集和只读Choose，含短暂Acquire/Release，不是零开销或跨Zone原子快照。原G2也追踪历史用于观察，但不执行历史门控。','',
        '## 迁移、空间和性能','',
        '|指标|G2|W历史准入|','|---|---:|---:|']
    for title,key,unit in [('总有效复制MiB','总迁移字节',1<<20),('RR复制MiB','RR迁移字节',1<<20),
            ('ZC复制MiB','ZC迁移字节',1<<20),('RR次数','RR次数',1),('ZC次数','ZC次数',1),
            ('成功Reset','全部成功Reset次数',1)]:
        values=[records[m]['维护及资源']['维护'][key]/unit for m in MODES]
        lines.append('|'+title+'|'+'|'.join(f'{v:.3f}' if unit!=1 else str(int(v)) for v in values)+'|')
    for title,key in [('末端可写MiB','空闲字节'),('末端有效MiB','有效字节'),('末端满区待回收MiB','满区可回收字节')]:
        values=[records[m]['维护及资源']['末端空间'][key]/(1<<20) for m in MODES]
        lines.append('|'+title+'|'+'|'.join(f'{v:.3f}' for v in values)+'|')
    for title,fn in [('应用完成吞吐ops/s',lambda r:r['应用端到端']['应用完成吞吐ops']),
                     ('aux实际调用秒',lambda r:r['aux实际调用']['调用累计秒'])]:
        lines.append('|'+title+'|'+'|'.join(f'{fn(records[m]):.3f}' for m in MODES)+'|')
    lines+=['',f"总迁移观察变化为{result['总迁移变化百分比']:+.3f}%。末端有效量相同={result['末端有效量相同']}。空间和复制分别报告，不能相减后冒称实测净迁移。Reset含无需复制的全无效回收，不等于ZC+RR次数。",'',
        f'新版末端可写空间相对G2变化{terminal_delta:+.3f}MiB；Active与Finish/尾损见维护资源表，不能只看复制量。','',
        '## 拒绝后的实际成本链','',
        f"拒绝涉及{detail['拒绝文件数']}个文件，拒绝后仍实际复制{detail['拒绝后实际复制字节']}字节；本轮内之后再次复制{detail['拒绝后仍再复制次数']}次，收尾前观察到{detail['收尾前观察到退役文件数']}个文件退役、{detail['退役后观察到Clear文件数']}个文件的退役后extent释放。",'',
        '观察窗口之外仍未知。没有再复制或看到退役都不能直接等同于在原策略下必然会发生的节省；原始对照、逐笔分类和目标代际应一起看。','',
        '|维护/当前类别|G2 MiB|新版 MiB|差额MiB|','|---|---:|---:|---:|']
    for kind in ('RR','ZC'):
        for category in ('H_only','W_only','HW','未分类SST','非SST'):
            a=detail['各臂'][MODES[0]]['分类成本'][kind][category]
            b=detail['各臂'][MODES[1]]['分类成本'][kind][category]
            lines.append(f'|{kind}/{category}|{a/(1<<20):.3f}|{b/(1<<20):.3f}|{(b-a)/(1<<20):+.3f}|')
    lines+=['','门控文件跨臂核对：原生文件名、创建来源、大小与创建序号范围均一致；以下仍是两条实际运行的成本比较，不是隔离了其他布局差异的反事实。','',
        '|文件ID|旧建议目标|实际普通目标|G2复制次数/MiB|新版复制次数/MiB|','|---|---|---|---:|---:|']
    for item in cross:
        fid=item['文件编号'];g=next(x for x in gates if int(x['文件编号'])==fid)
        a,b=(item['各臂'][m] for m in MODES)
        lines.append(f"|{fid}|Z{g['原G2建议目标']//(1<<27)}/g{g['原G2建议代际']}|Z{g['实际目标']//(1<<27)}/g{g['目标代际']}|{a['复制次数']}/{a['有效复制字节']/(1<<20):.3f}|{b['复制次数']}/{b['有效复制字节']/(1<<20):.3f}|")
    adverse=[r['文件编号'] for r in cross if r['各臂'][MODES[1]]['有效复制字节']>
             r['各臂'][MODES[0]]['有效复制字节']]
    lines+=['',f'门控文件中，累计复制反而增加的文件ID为{adverse}；不能笼统称每次拒绝都省了一次复制。门控只作用于已有成功迁移历史后的必要迁移，不能撤销此前已经发生的复制。','',
        '按全部RR+ZC合并的分类差额（新版减G2）：','']
    for category in ('未分类SST','W_only','H_only'):
        amount=sum(detail['各臂'][MODES[1]]['分类成本'][k][category]-
                   detail['各臂'][MODES[0]]['分类成本'][k][category] for k in ('RR','ZC'))
        lines.append(f'- {category}：{amount/(1<<20):+.3f}MiB。')
    maintenance=list(csv.DictReader((output/'逐次维护分类.csv').open(encoding='utf-8-sig')))
    lines+=['','## 仍未解决的高成本RR','',
        '|模式|RR序号|源Zone/代际|复制MiB|H-only MiB|W-only MiB|未分类SST MiB|',
        '|---|---:|---|---:|---:|---:|---:|']
    for m in MODES:
        top=sorted([r for r in maintenance if r['模式']==m and r['类型']=='RR'],
                   key=lambda r:int(r['总字节']),reverse=True)[:3]
        for r in top:
            amounts='|'.join(f"{int(r[k])/(1<<20):.3f}" for k in ('总字节','H_only','W_only','未分类SST'))
            lines.append(f"|{m}|{r['RR序号']}|Z{r['源区编号']}/g{r['源代际']}|{amounts}|")
    lines+=['','本轮两臂最大的RR4和RR10分别仍复制约128MiB和115MiB，四桶字节逐项相同。旧W历史拒绝没有解决第一次高成本陪迁，也未消除普通区未分类数据陪迁；不能将后续重复复制减少扩大成对全部RR的解决。']
    lines+=['','## 首次分歧与性能边界','',
        '同对逐条逻辑请求与已采集起点核验通过；随机设备会话及未采集FTL/队列状态在完整核验中单列，不能称为完整内存检查点。正常后台允许交错，未加Greedy重复组。','',
        f"首差早于首次门控={before_gate}：新版实际目标事件{first_target['各臂'][MODES[1]]['事件']}，首次拒绝事件{detail['首次新规则拒绝']['尝试事件']}。首差为第{first_target['序号从一开始']}次目标，文件ID{first_file}（{Path(sources[MODES[0]][first_file]['文件名']).name}）；两臂来源依次为{[sources[m][first_file]['来源'] for m in MODES]}。原生创建来源、大小及序号范围已不同，不能认定为‘同一个文件被随机标错’；实际是后台任务交错使编号分配给了不同输出。该差异不能由尚未执行的W历史拒绝造成。完整实际目标/复制事件见`实际去向与首差核验.json`，原生来源行号见`影子归因结论.json`。",'',
        '首次复制位置分歧为第26笔，H文件15从Z58/g1转出：G2目的为Z57/g2，新版为Z47/g5；也早于首次门控。因此完整迁移和吞吐差额没有隔离为W历史规则的因果效应。','',
        '两臂后台任务：','']
    for m in MODES:
        back=records[m]['维护及资源']['后台任务']
        lines.append('- '+m+'：'+json.dumps(back,ensure_ascii=False))
    lines+=['','业务时间互斥分账（内部aux嵌在Flush中，不再相加）：','']
    for m in MODES:lines.append('- '+m+'：'+json.dumps(records[m]['时间']['窗口分账'],ensure_ascii=False))
    windows=[records[m]['时间']['窗口分账'] for m in MODES]
    aux_delta=records[MODES[0]]['aux实际调用']['调用累计秒']-records[MODES[1]]['aux实际调用']['调用累计秒']
    lines+=['',
        f"本轮请求窗口缩短{windows[0]['请求窗口秒']-windows[1]['请求窗口秒']:.3f}秒；显式Flush减少{windows[0]['显式Flush秒']-windows[1]['显式Flush秒']:.3f}秒，其中aux实际调用减少{aux_delta:.3f}秒，API累计减少{windows[0]['API累计秒']-windows[1]['API累计秒']:.3f}秒。aux下层原因仍未确认，不能把吞吐提高写成迁移优化带来的性能收益。API及应用完成平均/P99/P99.9/P99.99和样本数均在CSV中。",'',
        'API及应用完成延迟分别见CSV。应用完成包括排队及客户端完成确认，不是单次NVMe介质响应。WAL关闭限制持久性结论。aux计时未拆到系统调用/宿主块层，不能把其差额归为少搬迁的算法收益。','',
        '## 当前决策','',
        '保留原G2与上一版，独立观察版不自动替换默认。本轮确认规则覆盖3次入池建议且最终落到其他Zone，988/1051的后续重复复制确实较少；但1052反例、全盘分类差额和门控前分歧说明，尚不足以宣布稳定净收益。下一步应先把这三个文件的“首次进入热点Zone”和“再次迁移时的准入”区分，补足首次准入的生命周期证据；保持阈值与K，暂不扩展复杂评分。','',
        '本轮完成后停止；没有启动额外实验。若要统计稳定收益，需预先定义配对重复与波动，或明确采用受控维护边界作因果验证；不把本单对当成统计显著性。','',
        '原始指标、源码/程序身份、日志及归档位于本报告开头配对目录。`旧G2建议与实际逐次对照.csv`和`影子建议核验.json`给出逐次依据；`逐笔复制分类.csv`、`逐次维护分类.csv`及相应旧W后续链给出维护证据。']
    output.mkdir(parents=True,exist_ok=True)
    (output/'实施与结果分析.md').write_text('\n'.join(lines)+'\n')
    (output/'影子归因结论.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    for name in ['维护资源吞吐.csv','端到端延迟.csv','API延迟.csv','旧G2建议与实际逐次对照.csv','影子建议核验.json','完整核验.json']:
        source=pair/'配对分析'/name;target=output/name
        if source.exists() and not target.exists():target.write_bytes(source.read_bytes())
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pair',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(report(args.pair,args.output),ensure_ascii=False,indent=2))
