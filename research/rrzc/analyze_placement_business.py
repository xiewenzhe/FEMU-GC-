"""三模式同负载消融，按真实账目和请求核验，不把入口事件当作收益。"""
import csv
import json
from pathlib import Path
import re
from analyze_integrated_pool import arm,REQUESTS
from analyze_all_blocks_dbbench import fields
from analyze_overlap import validate
from analyze_phase_time import analyze as time_audit
from analyze_end_to_end import analyze as end_audit
from analyze_placement_integration import analyze as composition_audit
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from ycsb_af import require


def greedy_decisions(root,unused):
    decisions=[]
    for line in (root/'zenfs.log').read_text().splitlines():
        if '共同观测实际决策：' not in line:continue
        row={k:int(v) for k,v in fields(line,'共同观测实际决策：').items()}
        require(row['执行Greedy']==1,'发生非Greedy执行')
        require(row['实际存在']==row['Greedy存在'],'实际候选存在性与Greedy不同')
        if row['实际存在']:require(row['实际Zone']==row['GreedyZone'],'实际Zone不是Greedy')
        decisions.append(dict(字段=row,改选=False,核验='实际执行日志；不重算未执行模型的信用或评分'))
    return decisions


def analyze(root,mode):
    report=arm(root,True,None,pool_k=int(mode!='G0'),decision_reader=greedy_decisions,concurrent=True)
    log=(root/'run.zenfs.log').read_text()
    require('放置消融配置：模式='+mode in log,'实际放置模式不符')
    composition=composition_audit(root/'组成.bin')
    # 原维护审计按成功有效extent复制统计；两种证据必须独立闭合。
    require(composition['有效复制字节']==report['维护']['总迁移字节'],'二进制复制与原维护账目不符')
    targets=composition['目标'];pool=[t for t in targets if t['协同目标']]
    hot_migrations=[t for t in pool if t['路径']=='必要迁移' and t['标签']&1]
    if mode=='G0':require(not pool,'G0进入协同池')
    if mode=='G1':require(not hot_migrations,'G1接受了H/HW必要迁移')
    return dict(通过=True,模式=mode,维护及资源=report,
        时间=time_audit(root,concurrent=True),应用端到端=end_audit(root),
        业务收尾=validate(root,report,end_to_end=True),
        放置事件=dict(协同目标取得=len(pool),Flush目标取得=sum(bool(t['标签']&2) for t in pool),
            H必要迁移入池=len(hot_migrations),H必要迁移拒绝=len(composition['G1拒绝']),
            Wonly必要迁移入池=sum(t['路径']=='必要迁移' and t['标签']==2 for t in pool),
            原生映射核验次数=composition['原生映射核验次数'],H发布交错=composition['存在H发布交错']),
        组成=composition)


def compare(a,b):
    start=initial(a,b);requests=request_comparison(a,b)
    start['完整检查点']='三模式各自新启动客机并从同一输入重建NAND/RC；不是完整内存检查点；随机设备会话原值单列'
    require(all(start[k]['相同'] for k in SEMANTIC),'跨组起点语义不一致')
    require(requests['逻辑提交相同'] and requests['完整成功请求数']==[REQUESTS]*2,'跨组请求首差')
    return dict(起点=start,请求=requests)


def finish(output):
    reports={mode:json.loads((output/mode/'业务核验.json').read_text()) for mode in ('G0','G1','G2')}
    roots={mode:output/mode/json.loads((output/mode/'状态.json').read_text())['标签']/'p1' for mode in reports}
    pairs={mode:compare(roots['G0'],roots[mode]) for mode in ('G1','G2')}
    metrics=[];latencies=[]
    for mode,r in reports.items():
        metrics.append(dict(模式=mode,**r['维护及资源']['维护'],**r['维护及资源']['资源'],
            应用完成吞吐ops=r['应用端到端']['应用完成吞吐ops'],**r['放置事件'],
            末端空闲字节=r['维护及资源']['末端空间']['空闲字节'],
            末端有效字节=r['维护及资源']['末端空间']['有效字节'],
            末端满区可回收字节=r['维护及资源']['末端空间']['满区可回收字节']))
        latencies.extend(dict(模式=mode,**row) for row in r['应用端到端']['指标'])
    dest=output/'完整消融分析';dest.mkdir()
    for name,rows in [('维护资源与吞吐.csv',metrics),('端到端平均与长尾.csv',latencies)]:
        with (dest/name).open('x',newline='') as f:
            writer=csv.DictWriter(f,list(rows[0]));writer.writeheader();writer.writerows(rows)
    changes={}
    for a,b in [('G0','G1'),('G1','G2'),('G0','G2')]:
        first=reports[a]['维护及资源']['维护']['总迁移字节'];last=reports[b]['维护及资源']['维护']['总迁移字节']
        changes[a+'→'+b]=dict(总迁移字节差=last-first,总迁移变化百分比=(last/first-1)*100 if first else None)
    result=dict(通过=True,跨组=pairs,差异=changes,
        边界='同程序、相同输入、每组独立新客机、单轮三模式；不与旧观测性能混算，不代表统计显著性或所有未来RR')
    (dest/'完整核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    text=['# G0/G1/G2完整业务消融','',result['边界'],'',
        '|指标|G0普通|G1 Flush隔离|G2完整放置|','|---|---:|---:|---:|']
    for name,key,scale in [('总迁移MiB','总迁移字节',1<<20),('ZC次数','ZC次数',1),('RR次数','RR次数',1),
        ('ZC迁移MiB','ZC迁移字节',1<<20),('RR迁移MiB','RR迁移字节',1<<20),
        ('成功Reset','全部成功Reset次数',1),('完成吞吐ops/s','应用完成吞吐ops',1)]:
        text.append('|'+name+'|'+'|'.join(f'{r[key]/scale:.3f}' for r in metrics)+'|')
    text+=['','G0→G1衡量Flush集中隔离贡献，G1→G2衡量已带H文件必要迁移入池的增量。'
        'G1池内的Flush文件仍可能后来变H，因此不是“完全无混放”的对照。'
        '延迟及长尾采用逐请求产生至最终完成确认；API延迟另存。'
        '末端空间、收尾复制及H发布交错必须结合原始核验判断，不能仅按RR次数宣称收益。','',
        '维护差异：`'+json.dumps(changes,ensure_ascii=False)+'`']
    (dest/'结果分析.md').write_text('\n'.join(text)+'\n')
    return result
