"""同窗有效RR成本的独立高精度核；不借助C++条件时间递推。"""
from decimal import Decimal,localcontext,ROUND_CEILING
from fractions import Fraction
from analyze_deadline import forecast as old_forecast
from replay_window_credit import MAX,SECOND
from replay_expected_rr import probability,SCALE

def forecast(values):
    old=old_forecast(values)
    risk,eta,_,_,_,reclaim,window,_,_=old
    z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs=values
    result=(risk,eta,0,0,v,reclaim,window,0,0)
    if not risk:return (*result,MAX,0,0)
    if rc>=threshold:return (1,0,v,v,0,reclaim,window,1,SCALE,0,0,0)
    if eta==MAX or not window:return (*result,MAX,0,0)
    life=min(min(MAX,v*ss//sl) if sl else MAX,min(MAX,v*ls//ll) if ll else MAX)
    event=min(window,life);rate=min(Fraction(d10,min(age,10*SECOND)),Fraction(gd,gs))
    mean=rate*event;known,units=probability(threshold-rc,mean)
    if not known or not units:return (risk,eta,0,0,v,reclaim,window,known,units,life,event,0)
    # 首达时间的一阶截断矩恒等式，与C++归一化尾项加权采用不同算法。
    with localcontext() as ctx:
        ctx.prec=80;m=threshold-rc;x=Decimal(mean.numerator)/Decimal(mean.denominator)
        term=(-x).exp();cdf=term
        for n in range(1,m):term=term*x/n;cdf+=term
        p=1-cdf;next_p=p-term*x/m
        conditional=Decimal(event)*m/x*next_p/p
        time=max(1,min(event,int(conditional.to_integral_value(rounding=ROUND_CEILING))))
    retained=max(0,v-max((sl*time+ss-1)//ss,(ll*time+ls-1)//ls))
    credit=retained*units//SCALE
    return (risk,eta,retained,credit,v-credit,reclaim,window,known,units,life,event,time)


# 以下只验收独立受控实验，不将其重解释为YCSB或正式性能结果。
def analyze_arm(root,mode,scenario,*,terminal=False,terminal_version=1):
    from pathlib import Path
    from collections import defaultdict
    import hashlib,json,re
    from analyze_joint_opportunity import fields,require,table
    from analyze_candidate_pool import strict_stats,qualification
    from analyze_window_credit import audit_scores
    from analyze_deadline import audit,audit_forecasts,choose
    from analyze_space_plan import demand
    from analyze_controlled_zc import ZC_STATS
    from analyze_maintenance_overlap import RR_STATS
    root=Path(root);enabled=mode=='joint'
    require(mode in ('space','joint') and scenario in ('opportunity','stopped','retire'),'未登记模式或场景')
    for n in ('wrapper.exit','run.exit','verify.exit'):
        require((root/n).read_text().strip()=='0','阶段失败：'+n)
    identity=(root/'software.before.log').read_text()
    require(identity and identity==(root/'software.after.log').read_text() and
        all(s.endswith(': OK') for s in identity.splitlines()),'业务期间软件身份改变')
    zones=json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(zones)==32 and all(z['state']=='EMPTY' and z['wp']==z['slba'] and z['cap']*512==128<<20
        for z in zones),'非全新4GiB/32区NAND')
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('[ERROR]' not in raw and '状态=失败' not in raw,'真实维护错误')
    require('严格RR主机配置：启用=1，' in raw and
        raw.count('时间一致配置：版本=1，启用=1，')==1,'严格RR或时间一致入口未实际启用')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',raw))=={'20'},'GC触发条件改变')
    require(not any(t in raw for t in ('空间紧急回收结果：','公共单区恢复维护结果：')),'混入应急选区')
    require(not terminal or raw.count(f'边界配置：版本={terminal_version}，启用=1，')==1,'边界新核没有真实启用')
    scores=audit_scores(lines,'window' if enabled else 'greedy',persistent=enabled,expected_rr=enabled,
        candidate_pool='expanded',space_plan=True,deadline=True,joint_time=True,terminal=terminal,terminal_version=terminal_version)
    if terminal:
        from analyze_terminal import audit
    predictions=audit_forecasts(lines,time_consistent=True)
    plans=audit(lines,version=terminal_version) if terminal else audit(lines)
    initial=table(root/'initial.csv');reads=table(root/'reads.csv')
    require([int(r['有效字节']) for r in initial]==[4<<20,(8 if scenario=='retire' else 5)<<20] and
        [int(r['RC']) for r in initial]==[0,928] and
        all(r['代际']=='0' and r['空闲百分比']=='18' for r in initial),'初态与受控登记不符')
    require(int(initial[1]['十秒真实增量'])==(0 if scenario=='stopped' else 928),'初始真实读历史不符')
    count=0 if scenario=='stopped' else 64 if scenario=='retire' else 128
    require(len(reads)==count and all(int(r['序号'])==n and int(r['计划纳秒'])==n*10**9//16 for n,r in enumerate(reads)),
        '固定读取安排不符')
    release=[i for i,s in enumerate(lines) if '联合维护释放：' in s]
    end=[i for i,s in enumerate(lines) if '联合维护观察结束：' in s]
    require(len(release)==len(end)==1 and release[0]<end[0],'观察边界不完整')
    prefix='\n'.join(lines[:release[0]])
    require(not ZC_STATS.search(prefix) and not RR_STATS.search(prefix),'准备阶段发生提前维护')
    body=lines[release[0]+1:end[0]]
    def ledger(part):
        zc=[tuple(map(int,m.groups())) for s in part if (m:=ZC_STATS.search(s))]
        rr=[tuple(map(int,m.groups())) for s in part if (m:=RR_STATS.search(s))]
        copies=[fields(s,'V1后续迁移复制：') for s in part if 'V1后续迁移复制：' in s]
        moved=sum(r[1] for r in zc+rr)
        require(all(r['复制成功']=='1' for r in copies) and sum(int(r['有效字节']) for r in copies)==moved,
            '逐文件和完成维护复制账目不闭合')
        resets=[tuple(map(int,m.groups())) for s in part if
            (m:=re.search(r'Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，',s))]
        require(all(c==b+1 for _,b,c in resets),'Reset代际不连续')
        return dict(ZC次数=sum(r[2] for r in zc),RR次数=sum(r[2] for r in rr),
            ZC复制字节=sum(r[1] for r in zc),RR复制字节=sum(r[1] for r in rr),总复制字节=moved,
            总成功ZoneReset=len(resets),推导Block擦除=len(resets)*128)
    whole,window=ledger(lines),ledger(body)
    before=json.loads((root/'strict.business.json').read_text());after=json.loads((root/'strict.after-verify.json').read_text())
    strict_stats(before);strict_stats(after);require(all(after[k]>=before[k] for k in before),'校验计数回退')
    require('只读校验通过：存活候选完整载荷' in (root/'verify.stdout.log').read_text() and
        '[ERROR]' not in (root/'zenfs.verify.log').read_text(),'真实载荷校验失败')
    rows=defaultdict(list);needs={};pool={}
    for s in lines:
        if 'GC 检查：' in s:pool={}
        if '候选范围资格：' in s:
            r=fields(s,'候选范围资格：');qualification(r,True);pool[int(r['Zone'])]=r
        if '时序成本：' in s:
            r=fields(s,'时序成本：');z=int(r['Zone'])
            require(z in pool and pool[z]['实际比例资格']=='1','成本中混入比例资格未通过者')
            rows[int(r['轮次'])].append(dict(z=z,v=int(r['有效']),cap=int(r['容量']),credit=int(r['RR复制']),
                p=int(r['概率十亿分数']),space=int(r['RR空间']),safe=int(r['安全'])))
        if '时序方案需求：' in s:
            r=fields(s,'时序方案需求：');needs[int(r['轮次'])]=demand(*(int(r[k]) for k in ('空闲','非空闲','分配等待')))
    actual=[p for p in plans if p['实际Zone'] is not None];require(actual,'没有实际ZC选区')
    first=actual[0];safe={r['z'] for r in rows[first['轮次']] if r['safe']}
    require(all(int(r['源Zone']) in safe for r in initial),'首轮没有两个真实安全候选')
    changes=[]
    for p in actual:
        n=p['轮次'];_,zero,_=choose([dict(r,p=0,credit=0,space=0) for r in rows[n]],needs[n])
        if terminal:zero=p['RR项置零Zone']
        if p['实际Zone']!=zero:changes.append(dict(轮次=n,实际Zone=p['实际Zone'],同轮RR置零Zone=zero))
    return dict(验收通过=True,场景=scenario,模式=mode,初态=initial,
        请求计划=[(r['序号'],r['计划纳秒']) for r in reads],首轮实际Zone=first['实际Zone'],
        RR项实际改选=changes,窗口内=window,全业务=whole,独立评分核验=scores,
        时间一致预测=predictions,空间方案=plans,严格RR业务账目=before,严格RR校验后账目=after,
        日志SHA256=hashlib.sha256(raw.encode()).hexdigest(),
        边界='真实受控25秒，不是YCSB；观察结束后至业务返回的维护单列，校验维护另记；不预知突发退役')

def compare(arms):
    from analyze_joint_opportunity import require
    require(set(arms)=={'space','joint'},'受控配对不完整')
    a,b=arms['space'],arms['joint']
    require(all(a[k]==b[k] for k in ('场景','初态','请求计划')),'真实初态或读取安排不同')
    return dict(配对验收通过=True,场景=a['场景'],模式=arms,
        窗口内差值={k:b['窗口内'][k]-a['窗口内'][k] for k in a['窗口内']},
        全业务差值={k:b['全业务'][k]-a['全业务'][k] for k in a['全业务']},
        边界='有限受控机会验证，不证明YCSB或稳态收益；必须同时核对空间、复制和真实改选')
