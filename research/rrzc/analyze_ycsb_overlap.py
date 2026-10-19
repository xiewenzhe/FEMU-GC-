"""分别核验空间预热和热读，不用两个阶段的累计次数冒充同时改选机会。"""
import re
from analyze_ycsb_retention import audit,measurements,sha,MIB
from analyze_v1_shadow_stream import require,fields
from analyze_v1_growth_stream import qualifies
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from replay_single_recovery import replay as single_replay
from replay_space_emergency import replay as space_replay
from guarded_rounds import parse
from retention_log import RetentionLog


def opportunities(lines):
    candidates={};blocks={};shorts={};examples=[];rounds=near_rounds=alternate_rounds=proposals=0
    for line in lines:
        if '简单双因素候选：' in line:
            row=fields(line,'简单双因素候选：');candidates[row['Zone']]=row
        if 'V1影子候选：' in line:
            row=fields(line,'V1影子候选：');blocks[row['Zone']]=row
        if '独立约束候选：' in line:
            row=fields(line,'独立约束候选：');shorts[row['Zone']]=row
        if '独立约束决策：' not in line:continue
        decision=fields(line,'独立约束决策：');rounds+=1
        require(candidates.keys()==blocks.keys()==shorts.keys(),'机会统计缺候选记录')
        near=[];alternatives=[]
        for zone,c in candidates.items():
            s=shorts[zone]
            if not (qualifies(c) and s['短期预测可知']=='1' and int(s['短期达阈值纳秒'])<=10_000_000_000):continue
            near.append(zone)
            if decision['存在Greedy']=='1' and zone!=decision['GreedyZone']:alternatives.append(zone)
            examples.append(dict(轮次=decision['轮次'],候选=c,同块统计=blocks[zone],短期统计=s,GreedyZone=decision['GreedyZone']))
        near_rounds+=bool(near);alternate_rounds+=bool(alternatives)
        proposals+=decision['原会改选']=='1'
        candidates={};blocks={};shorts={}
    require(not candidates and not blocks and not shorts,'机会统计末尾候选未闭合')
    return dict(正常ZC决策轮数=rounds,包含临RR合格候选轮数=near_rounds,
        包含非Greedy临RR候选轮数=alternate_rounds,影子会改选轮数=proposals,候选证据=examples)


def analyze(root):
    evaluation=audit(root,'greedy',nowal=True,expected_ops=600000,
        single_recovery=True,space_emergency=True,inherited_audit=True)
    warmup=root/'warmup'
    require((warmup/'business.exit').read_text().strip()=='0','预热业务失败')
    properties=dict(s.split('=',1) for s in (warmup/'workload.properties').read_text().splitlines()
        if '=' in s and not s.startswith('#'))
    for key,value in dict(recordcount='655360',operationcount='1200000',readproportion='0',
        updateproportion='1',requestdistribution='uniform',insertorder='ordered',zeropadding='20',
        **{'rocksdb.disable_wal':'true','rocksdb.fs_uri':'zenfs://dev:nvme0n1','limit.ops':'2000'}).items():
        require(properties.get(key)==value,'预热配置不符：'+key)
    ycsb=(warmup/'ycsb.log').read_text()
    timing=measurements(ycsb,1200000,('UPDATE',))
    require(ycsb.count('实验WAL配置：关闭=1')==1,'预热未确认无WAL')
    counts=dict(s.split('=',1) for s in (warmup/'audit.counts').read_text().splitlines())
    require(counts=={'装载':'655360','读取':'0','更新':'1200000','键数':'655360'},'预热计数不符')
    text=(warmup/'zenfs.run.log').read_text();lines=text.splitlines()
    require(not any('[ERROR]' in s or '状态=失败' in s for s in lines),'预热存在维护错误')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',text))=={'20'},'预热GC条件未确认')
    filtered,_=space_replay(lines);filtered,_=single_replay(filtered)
    if '独立约束决策：' in text:
        decisions,_,_=parse(filtered,'greedy',cost_rank=False,proposal_model=RetentionLog(False))
        require(all(d['实际改选']=='0' for d in decisions),'预热不应执行改选')
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    copies=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr)
    require(sum(int(c['有效字节']) for c in copies if c['复制成功']=='1')==moved,'预热复制账目不符')
    require(text.count('RR 开始：')==len(rr) and text.count('RR 完成：')==sum(r[2] for r in rr),'预热RR账目不符')
    require(all(r[2]==1 for r in zc+rr),'预热存在未完成源Reset')
    phase=dict(含初始装载=True,业务更新数=1200000,调用延迟=timing,
        ZC重置=sum(r[2] for r in zc),RR重置=sum(r[2] for r in rr),总迁移MiB=moved/MIB,
        请求轨迹SHA256=sha(warmup/'audit.requests'),最新值清单SHA256=sha(warmup/'audit.expected'))
    evaluation_lines=(root/'zenfs.run.log').read_text().splitlines()
    filtered,_=space_replay(evaluation_lines);filtered,_=single_replay(filtered)
    opportunity=opportunities(filtered)
    ready=(evaluation['验收通过'] and evaluation['RR重置']>0 and evaluation['ZC重置']>0
        and opportunity['包含非Greedy临RR候选轮数']>0 and opportunity['影子会改选轮数']>0)
    return dict(验收通过=evaluation['验收通过'],预热含装载=phase,热读阶段=evaluation,
        热读阶段机会=opportunity,具备进入机制配对条件=ready,
        说明='同一次FEMU启动、同一数据库，两次YCSB进程且请求分布分阶段改变；预热不计为热读维护。实际仍Greedy，近RR是当前估计，不是已证明未来必然RR；不是收益或稳态结论。')
