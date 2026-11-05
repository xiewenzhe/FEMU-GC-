"""核验后台重叠、相同请求及跨业务/收尾事务；不强求维护结果一致。"""
import csv
import json
from pathlib import Path
import re
from ycsb_af import require,digest
from analyze_phase_time import analyze


def split_ledger(log,transactions):
    lines=log.splitlines()
    markers=[(i,re.search(r'前后台重叠维护边界：阶段=(\d+)，请求=(\d+)，单调纳秒=(\d+)',line))
             for i,line in enumerate(lines,1) if '前后台重叠维护边界：' in line]
    require(len(markers)==2 and [int(m[1]) for _,m in markers]==[1,2],'维护窗边界缺失或重复')
    cutoff=markers[0][0];cutoff_tx=cutoff+1
    # 通用事务审计在原日志前添加一行窗口说明，事务行号因此比原始日志大一。
    result={n:dict(ZC完成次数=0,RR完成次数=0,ZC复制字节=0,RR复制字节=0,成功Reset=0)
                               for n in ('业务','收尾')}
    crossed=[]
    for tx in transactions:
        kind=tx['类型'];endphase='业务' if tx['结束行']<cutoff_tx else '收尾'
        result[endphase][kind+'完成次数']+=1
        if tx['开始行']<cutoff_tx<tx['结束行']:crossed.append(dict(类型=kind,Zone=tx['Zone'],开始行=tx['开始行'],结束行=tx['结束行']))
        for row in tx['复制']:
            phase='业务' if row['行']<cutoff_tx else '收尾'
            result[phase][kind+'复制字节']+=int(row['字段']['有效字节'])
    for i,line in enumerate(lines,1):
        if 'Zone 重置诊断：' in line and '状态=成功' in line:
            result['业务' if i<cutoff else '收尾']['成功Reset']+=1
    return dict(按日志提交顺序分账=result,跨窗事务=crossed,
        边界行=[i for i,_ in markers],请求数=[int(m[2]) for _,m in markers],
        边界单调纳秒=[int(m[3]) for _,m in markers],
        口径='复制按成功复制日志所在侧计量，事务次数按完成侧计量；跨窗事务明确列出。收尾含剩余Flush/Compaction及关闭前维护，不声称已观察所有未来RR。')


def validate(root,report,end_to_end=False):
    run=(root/'run.log').read_text();log=(root/'run.zenfs.log').read_text()
    require('受控GC批次检查：启用=1' not in log,'仍启用批次GC门控')
    require('受控后台线程：HIGH=0' not in run,'仍共用单后台线程')
    pattern=(r'应用队列配置：单消费线程=1，同步批提交=1，Compaction排空=0，批次GC=0，HIGH=(\d+)，LOW=(\d+)'
        if end_to_end else r'前后台重叠配置：单前台=1，异步Flush=1，业务排空=0，批次GC=0，HIGH=(\d+)，LOW=(\d+)')
    cfg=re.findall(pattern,run)
    require(len(cfg)==1 and all(int(n)>=1 for n in cfg[0]),'真实后台线程配置不符')
    bounds=re.findall(r'前后台重叠业务边界：阶段=(\d+)，请求=(\d+)，单调纳秒=(\d+)',run)
    require(len(bounds)==2 and [int(x[0]) for x in bounds]==[1,2] and all(int(x[1])==2260992 for x in bounds),'完整业务边界不符')
    ledger=split_ledger(log,report['事务']);parts=ledger['按日志提交顺序分账']
    require(ledger['边界单调纳秒']==[int(x[2]) for x in bounds],'客户端与维护日志边界时钟不一致')
    total=report['维护']
    for kind in ('ZC','RR'):
        require(sum(p[kind+'复制字节'] for p in parts.values())==total[kind+'迁移字节'],'分阶段复制与全程账目不同')
        require(sum(p[kind+'完成次数'] for p in parts.values())==total[kind+'次数'],'分阶段维护次数不同')
    require(sum(p['成功Reset'] for p in parts.values())==total['全部成功Reset次数'],'分阶段Reset不闭合')
    t=analyze(root,concurrent=True)['窗口分账']
    cancelled=sum(int(copy['字段']['有效字节']) for tx in report['事务'] for copy in tx['复制']
        if any(c['字段']['文件编号']==copy['字段']['文件编号'] for c in tx.get('退役撤销',[])))
    return dict(通过=True,配置=cfg[0],维护分账=ledger,退役撤销复制字节=cancelled,
        显式后台排空秒=(int(bounds[1][2])-int(bounds[0][2]))/1e9,
        请求窗口=t,后台任务=report['后台任务'],
        源码边界='后台重叠以实际原生任务区间与业务时间线为证据，不要求两策略具有相同任务/维护次数')


def finish(output,end_to_end=False):
    dest=output/'前后台重叠分析';dest.mkdir()
    reports={t:json.loads((output/(t+'-核验.json')).read_text()) for t in ('p1','m1')}
    times={t:json.loads((output/(t+'-时间核验.json')).read_text()) for t in reports}
    phases={t:json.loads((output/(t+'-业务收尾核验.json')).read_text()) for t in reports}
    rows=[];lat=[];details=[]
    for t,r in reports.items():
        rows.append(dict(臂=t,**r['维护'],**r['计时'],**r['资源'],
            **{k:v for k,v in r['末端空间'].items() if k!='Zone'},显式排空秒=phases[t]['显式后台排空秒'],
            后台重叠次数=r['后台任务']['重叠次数']))
        lat.extend(dict(臂=t,**p) for p in times[t]['API延迟'])
        details.extend(dict(臂=t,**p) for p in times[t]['内部区间'])
    for name,data in (('维护资源与吞吐.csv',rows),('API延迟.csv',lat),('内部区间.csv',details)):
        with (dest/name).open('x',newline='') as f:
            w=csv.DictWriter(f,list(data[0]));w.writeheader();w.writerows(data)
    evidence=dict(通过=True,来源=str(output),各臂=reports,时间=times,业务收尾=phases,
        边界=('有界应用队列4096；写2048批次同步Flush后完成；FIFO消费与正常后台调度；'
              if end_to_end else '单前台固定逻辑流；异步Flush和正常后台调度；')+
             '一对实跑，无额外Greedy重复；非多客户端并发，非统计显著性证据')
    (dest/'完整核验.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    lines=['# 前后台重叠：普通放置与协同放置','',evidence['边界'],'',
        '|指标|普通放置p1|协同放置m1|','|---|---:|---:|']
    for name,fn in (
        ('ZC＋RR总迁移MiB',lambda t:reports[t]['维护']['总迁移字节']/(1<<20)),
        ('请求窗口吞吐ops/s',lambda t:times[t]['窗口分账']['吞吐ops']),
        ('请求窗口秒',lambda t:times[t]['窗口分账']['请求窗口秒']),
        ('显式排空秒',lambda t:phases[t]['显式后台排空秒']),
        ('Flush/Compaction重叠次数',lambda t:reports[t]['后台任务']['重叠次数'])):
        lines.append(f'|{name}|{fn("p1"):.6f}|{fn("m1"):.6f}|')
    lines+=['','业务与收尾复制按成功日志所在侧分账，跨窗事务单列，完整账目包括关闭前维护。'
        '业务结束不代表所有未来RR已经观察。平均与长尾API、末端空间、Reset/Finish及内部区间保留在同目录CSV/JSON。']
    (dest/'结果分析.md').write_text('\n'.join(lines)+'\n')
    return times
