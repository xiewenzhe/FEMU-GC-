"""独立整数重算空间需求与方案；计划区不视为实际完成的维护。"""
from fractions import Fraction
from analyze_joint_opportunity import fields,require

def demand(free,non_free,waiters,percent=20):
    known=0<free+non_free<=2**64-1 and 0<=percent<=100
    target=((free+non_free)*percent+99)//100 if known else 0
    return known,target,max(target-free,int(bool(waiters)),0) if known else 0

def choose(rows,rr_space,need,*,first_cost=False):
    known,_,gap=need
    if known and gap==0:return [],None,False
    safe=[r for r in rows if int(r['安全']) and 0<int(r['有效'])<int(r['有效'])+int(r['回收'])]
    safe.sort(key=lambda r:(Fraction(int(r['有效']),int(r['回收'])),int(r['有效']),int(r['Zone'])))
    plans=[]
    for first in safe:
        members=[first];reclaimed=int(first['回收'])
        for r in safe:
            if reclaimed>=gap:break
            if r is not first:members.append(r);reclaimed+=int(r['回收'])
        selected={int(r['Zone']) for r in members}
        copy=sum(int(r['有效']) for r in members)
        future=sum(int(r['RR抵扣']) for r in safe if int(r['Zone']) not in selected)
        space=sum(rr_space.get(int(r['Zone']),0) for r in safe if int(r['Zone']) not in selected)
        plans.append(dict(首区=int(first['Zone']),足够=int(known and reclaimed>=gap),
            计划区数=len(members),计划区='/'.join(r['Zone'] for r in members),当前复制=copy,
            当前回收=reclaimed,未选预计RR复制=future,未选预计RR空间=space,预计总复制=copy+future,
            实际执行仅首区=1))
    sufficient=[p for p in plans if p['足够']]
    if sufficient:
        by_zone={int(r['Zone']):r for r in safe}
        def priority(p):
            first=by_zone[p['首区']]
            return (p['预计总复制'],p['当前复制'],p['计划区数'],
                *((int(first['有效']),-int(first['回收'])) if first_cost else ()),
                -p['当前回收']-p['未选预计RR空间'],p['首区'])
        best=min(sufficient,key=priority)
        return plans,best['首区'],False
    progress=min(safe,key=lambda r:(-int(r['回收']),Fraction(int(r['有效']),int(r['回收'])),
        int(r['有效']),int(r['Zone']))) if safe else None
    return plans,int(progress['Zone']) if progress else None,bool(progress)

def audit_plans(lines):
    configs=[fields(s,'空间方案配置：') for s in lines if '空间方案配置：' in s]
    if configs:
        require(len(configs)==1 and configs[0]['版本'] in ('1','2'),'未知空间方案版本')
    first_cost=bool(configs and configs[0]['版本']=='2')
    tags=('统一成本','空间方案RR空间','空间方案需求','空间方案候选','空间方案决策')
    groups={t:{} for t in tags}
    for line in lines:
        for tag in tags:
            if tag+'：' not in line:continue
            r=fields(line.split('，原因=',1)[0],tag+'：');n=int(r['轮次'])
            groups[tag].setdefault(n,[]).append(r)
    require(set(groups['空间方案需求'])==set(groups['空间方案决策']),'需求与实际决策轮次不闭合')
    result=[]
    for n,items in groups['空间方案需求'].items():
        require(len(items)==len(groups['空间方案决策'][n])==1,'同轮需求或决策重复')
        r=items[0];need=demand(*(int(r[k]) for k in ('空闲','非空闲','分配等待','目标百分比')))
        require(int(r['目标百分比'])==20,'空间目标不是20%')
        require((bool(int(r['可知'])),int(r['目标字节']),int(r['缺口字节']))==need,'空间需求重算不符')
        spaces={}
        for x in groups['空间方案RR空间'].get(n,[]):
            z=int(x['Zone']);require(z not in spaces,'RR空间诊断重复')
            space=(int(x['容量'])-int(x['预计留存']))*int(x['概率十亿分数'])//10**9 if int(x['概率可知']) else 0
            require(space==int(x['预计RR回收']),'RR空间贡献重算不符');spaces[z]=space
        rows=groups['统一成本'].get(n,[])
        require(len({r['Zone'] for r in rows})==len(rows),'评分候选重复')
        plans,zone,progress=choose(rows,spaces,need,first_cost=first_cost)
        logged=groups['空间方案候选'].get(n,[])
        require(len(logged)==len(plans),'完整方案数量不符')
        by_first={int(r['首区']):r for r in logged}
        require(len(by_first)==len(logged),'方案首区重复')
        for p in plans:
            require(p['首区'] in by_first,'缺首区方案')
            actual=by_first[p['首区']]
            require(all((actual[k]==v if isinstance(v,str) else int(actual[k])==v) for k,v in p.items()),
                '方案当前/后续复制与空间重算不符')
        decision=groups['空间方案决策'][n][0]
        require((bool(int(decision['实际存在'])),int(decision['实际Zone']),bool(int(decision['仅进展'])))==
            (zone is not None,zone if zone is not None else 0,progress),'实际选区没有遵循方案核')
        result.append(dict(轮次=n,缺口字节=need[2],实际Zone=zone,仅进展=progress,方案=plans))
    return result
