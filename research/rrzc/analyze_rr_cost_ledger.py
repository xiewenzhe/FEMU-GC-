"""固定完整短测的离线成本账本；不生成未来概率、不执行改选、不把潜在成本当收益。"""
import hashlib,json
from collections import Counter
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from analyze_rocksdb_anchor_slow import LABEL,audit as classified_audit,category
from analyze_maintenance_overlap import RR_START,RR_STATS
from analyze_v1_shadow_stream import fields,require

MIB=1<<20

def current_cost(candidate,greedy):
    """仅比较两份选区快照；净回收为逻辑量，不冒充实际空区或尾部消耗。"""
    v,c,g,gc=(int(r[k]) for r,k in ((candidate,'有效字节'),(candidate,'容量字节'),
                                   (greedy,'有效字节'),(greedy,'容量字节')))
    require(0<g<gc and 0<v<c,'有效量或净回收空间非法')
    require(v>=g,'合格候选比Greedy有效量更低，需先核查基线')
    extra=v-g;reclaim=c-v;greedy_reclaim=gc-g
    return dict(候选有效字节=v,Greedy有效字节=g,额外源有效搬运字节=extra,
        候选逻辑净回收字节=reclaim,Greedy逻辑净回收字节=greedy_reclaim,
        相对Greedy少回收逻辑字节=greedy_reclaim-reclaim,
        同容量=c==gc,同容量时搬运差与回收差相等=(extra==greedy_reclaim-reclaim) if c==gc else None,
        候选单位净回收逻辑搬运系数=str(Fraction(v,reclaim)),
        Greedy单位净回收逻辑搬运系数=str(Fraction(g,greedy_reclaim)),
        候选单次RR当前有效量代理字节=v,Greedy单次RR当前有效量代理字节=g,
        已确认可避免RR字节=None,反事实净收益字节=None,
        口径='额外搬运与少回收空间分栏，不重复相加。RR代理仅表示按当前有效量搬一次的规模，不表示未来必然RR或一定能省。')

def stable_generation(observation):
    generations=observation['主机代际'].split('/')
    return generations[0] if observation['重置窗稳定']=='1' and len(generations)==3 and len(set(generations))==1 else None

def wall_time(line):
    return datetime.strptime(line[:26],'%Y/%m/%d-%H:%M:%S.%f')

def collect_context(lines):
    """只保留原日志给出的身份；主机与设备Reset代际不做数值互比。"""
    geometry={};blocks={};hosts={};tails={};rr=[];observed={};pending=None;current_candidates=[]
    for number,line in enumerate(lines,1):
        if 'GC 检查：' in line:observed={}
        if 'Zone 维护观测：' in line:
            o=fields(line,'Zone 维护观测：')
            if o['阶段']=='ZC候选':observed[o['Zone']]=o
        if 'V1影子候选：' in line:
            b=fields(line,'V1影子候选：');key=b['轮次'],b['Zone']
            require(key not in blocks and b['Zone'] in observed,'当前点重复或缺主机代际')
            blocks[key]=dict(b,行号=number,日志时钟=line[:26])
            hosts[key]=stable_generation(observed[b['Zone']])
        if '双点速率建议：' in line:
            d=fields(line,'双点速率建议：')
            # 同轮空间候选先于近期点写出，由独立完整核验负责验证资格。
            for c in current_candidates:
                geometry[d['轮次'],c['Zone']]=c
            current_candidates=[]
        if '简单双因素候选：' in line:
            current_candidates.append(fields(line,'简单双因素候选：'))
        if '独立约束决策：' in line:
            d=fields(line,'独立约束决策：');tails[d['轮次']]=d
        if (m:=RR_START.search(line)):
            require(pending is None,'RR开始未闭合')
            pending=dict(行号=number,Zone=m.group(1),开始时钟=line[:26],源有效字节=int(m.group(3)))
        if (m:=RR_STATS.search(line)):
            require(pending is not None,'RR统计缺开始')
            rr.append(dict(pending,实际迁移字节=int(m.group(2)),重置区数=int(m.group(3))))
            pending=None
    require(pending is None and not current_candidates,'末次RR或候选缺闭合记录')
    return geometry,blocks,hosts,tails,{r['行号']:r for r in rr}

def rr_followup(round_id,zone,host_generation,block,maintenance,copies):
    """只选原候选之后且同主机代际的RR；没有事件保持收益未知。"""
    out=[]
    for relation in maintenance['同地址后续RR核对']:
        c,r=relation['候选'],relation['后续RR']
        if str(c['轮次'])!=round_id or c['Zone']!=zone:continue
        if host_generation is None or not relation['同一主机代际'] or r['主机代际']!=host_generation:continue
        require(r['行号'] in copies,'RR缺复制统计')
        copy=copies[r['行号']]
        require(copy['Zone']==zone and copy['行号']>block['行号'],'RR身份或顺序不符')
        delay=(wall_time(copy['开始时钟'])-wall_time(block['日志时钟'])).total_seconds()
        require(delay>=0,'同客机墙钟倒退，不生成后续标签')
        out.append(dict(copy,距候选秒=delay,主机代际=host_generation))
    return dict(观察内同代RR=out,观察内RR实际迁移字节=sum(r['实际迁移字节'] for r in out) if out else None,
        因果边界='这是Greedy轨迹的实际后续维护，不是改选后的反事实；未见事件不补零，观察内搬运也不自动视为全部可避免。')

def audit(root):
    require(root.name==LABEL,'只核对固定完整限速短测')
    classified=classified_audit(root)
    require(classified==json.loads((root/'机会分类复核.json').read_text()),'原机会分类复算不一致')
    saved=json.loads((root/'独立核验.json').read_text());paired=saved['双点速率核验']
    run=root/'运行归档'/LABEL;lines=(run/'zenfs.run.log').read_text().splitlines()
    geometry,blocks,hosts,tails,copies=collect_context(lines)
    output=[]
    for decision in paired['影子建议']:
        rid=decision['轮次'];candidates=[c for c in paired['候选观察'] if c['轮次']==rid and c['合格']]
        if decision['存在Greedy']!='1':
            require(not candidates,'无Greedy却存在合格候选');continue
        greedy=next(c for c in candidates if c['是Greedy']);gkey=rid,greedy['Zone']
        for candidate in candidates:
            key=rid,candidate['Zone'];b=blocks[key]
            costs=current_cost(geometry[key],geometry[gkey])
            selected=decision['会改选']=='1' and candidate['Zone']==decision['建议Zone']
            if selected:require(costs['额外源有效搬运字节']==int(decision['额外有效字节']),'搬运差与影子建议不符')
            old=tails[rid]
            same_suggestion=selected and old['原会改选']=='1' and old['短期建议Zone']==candidate['Zone'] and old['GreedyZone']==greedy['Zone']
            output.append(dict(轮次=rid,Zone=candidate['Zone'],是Greedy=candidate['是Greedy'],
                是双点影子改选建议=selected,候选日志时钟=b['日志时钟'],主机代际=hosts[key],
                设备会话=b['设备会话'],设备代际=b['设备代际'],当前最大块=b['块编号'],
                RR状态=category(candidate),GreedyRR状态=category(greedy),
                当前成本=costs,当前双点证据=candidate['双点证据'],
                同区旧路径尾部及源复核通过=(old['复核后可改选']=='1') if same_suggestion else None,
                后续维护=rr_followup(rid,candidate['Zone'],hosts[key],b,saved['维护事实'],copies)))
    suggestions=[r for r in output if r['是双点影子改选建议']]
    identities={(r['Zone'],r['设备会话'],r['设备代际'],r['主机代际']) for r in suggestions}
    require(len(identities)==classified['建议去重区代际数'],'两种口径的建议去重不一致')
    return dict(完整核验及机会分类复算一致=True,候选成本账本=output,
        影子建议成本=[r for r in suggestions],建议次数=len(suggestions),建议区代际数=len(identities),
        建议后观察到同代RR的次数=sum(bool(r['后续维护']['观察内同代RR']) for r in suggestions),
        合格Greedy自身已达阈值轮数=sum(r['是Greedy'] and r['RR状态']=='当前已经达阈值' for r in output),
        候选分类=dict(Counter(r['RR状态'] for r in output)),
        模型约束说明=[
            '当前有效量与净回收空间有确定关系；同容量下单位净回收搬运系数与有效量排序相同，不是新的独立信号。',
            '额外搬运是立即支出，少回收空间是空间压力，分别报告，不未经标定就重复折算成迁移字节。',
            '当前有效量只给出一次RR搬运规模代理；RR何时执行、数据能否存活到该时刻仍需独立证据。',
            'Greedy本来就会清除的RR风险必须计入参照；不能把新策略的全部RR重叠当成相对收益。',
            '同一Zone同代多轮建议是不同决策分支，第一次改选会改变后续轨迹，不能累加成一次运行的成本或收益。',
            '不把ETA为零当成立即执行RR，不把零近期增量当成没有待处理RR，不把末尾截断当成永不RR。'],
        输入身份={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ('独立核验.json','机会分类复核.json')},
        分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        未修改='生产代码、GC和RR条件、16MiB预算、候选资格、负载、原始数据或既有报告；未启动新客机。',
        结论边界='离线当前成本和实际Greedy后续分栏，不构造未来概率，不输出新选区分数，未确认改选的净收益。')

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要固定限速短测归档根目录');root=Path(sys.argv[1]).resolve()
    output=root/'成本收益账本.json';require(not output.exists(),'输出已存在，拒绝覆盖')
    output.write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
    print('当前成本与Greedy后续账本核验通过，未将未知RR收益补成零或正值。')
