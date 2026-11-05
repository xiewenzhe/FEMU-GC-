"""边界成本核的独立整数重算；历史重放只说明评分变化，不是新设备实验。"""
from fractions import Fraction
from analyze_deadline import choose as previous_choose,SCALE

def terminal_valid(row,history,window=10**10):
    if history is None:return False,row['v']
    k=('留存可知','留存Zone','留存代际','留存有效','短减少','短间隔','长减少','长间隔')
    good,z,g,v,sl,ss,ll,ls=(int(history[n]) for n in k)
    if not (good and z==row['z'] and g==row['generation'] and v==row['v'] and
            0<window<=10**10 and 0<ss<=ls<=32*10**9 and ls>=window and
            0<=sl<=ll<=row['cap']-v):return False,row['v']
    return True,max(0,v-max((sl*window+ss-1)//ss,(ll*window+ls-1)//ls))

def choose(rows,need):
    old,zone,progress=previous_choose(rows,need)
    result=dict(found=zone is not None,zone=zone or 0,progress=progress,known=False,reference=0,plans=[])
    if not need[0] or not need[2] or zone is None or progress:return result
    pool=[r for r in rows if r['safe'] and 0<r['v']<r['cap'] and
        0<=r['p']<=SCALE and 0<=r['credit']<=r['v']]
    if len({r['z'] for r in pool})!=len(pool) or sum(r['cap'] for r in pool)>2**64-1:return result
    for r in pool:
        if not (0<=r['retained']<=r['v'] and r['credit']==r['retained']*r['p']//SCALE and
            r['space']==(r['cap']-r['retained'])*r['p']//SCALE and
            (not r['known'] or 0<=r['terminal']<=r['v'])):return result
    for base in old:
        members={int(v) for v in base['计划区'].split('/')}
        space=sum(r['cap']-r['v'] if r['z']==base['首区'] else r['space']+
            ((r['cap']-r['v'])*(SCALE-r['p'])//SCALE if r['z'] in members else 0) for r in pool)
        result['plans'].append(dict(first=base['首区'],space=space,gap=0,deferred=0,
            score=base['预计总复制'],priced=False,completion=[],base=base))
    result['reference']=max(p['space'] for p in result['plans'] if p['base']['足够'])
    for plan in result['plans']:
        if not plan['base']['足够']:continue
        plan['gap']=remaining=result['reference']-plan['space'];available=[]
        members={int(v) for v in plan['base']['计划区'].split('/')}
        for r in pool:
            if r['z'] in members:continue
            v=r['terminal'] if r['known'] else r['v'];non_rr=SCALE-r['p']
            space=(r['cap']-v)*non_rr//SCALE;copy=(v*non_rr+SCALE-1)//SCALE
            if space:available.append((r['z'],space,copy))
        for z,space,copy in sorted(available,key=lambda t:(Fraction(t[2],t[1]),t[0])):
            if not remaining:break
            taken=min(space,remaining);priced=(copy*taken+space-1)//space
            remaining-=taken;plan['deferred']+=priced
            plan['completion'].append((z,space,copy,taken,priced))
        plan['score']+=plan['deferred'];plan['priced']=not remaining
        if remaining:return result
    result['known']=True;by_id={r['z']:r for r in pool}
    best=min((p for p in result['plans'] if p['base']['足够']),key=lambda p:
        (p['score'],p['base']['预计RR复制'],by_id[p['first']]['v'],p['base']['计划区数'],p['first']))
    result['zone']=best['first'];return result

def inputs(lines):
    from analyze_joint_opportunity import fields
    from analyze_space_plan import demand
    rows={};history={};generations={};need={}
    for s in lines:
        if 'V1影子候选：' in s:
            r=fields(s,'V1影子候选：');generations[int(r['轮次']),int(r['Zone'])]=int(r['设备代际'])
        if '窗口收益成本：' in s:
            r=fields(s.split('，原因=',1)[0],'窗口收益成本：');history[int(r['轮次']),int(r['Zone'])]=r
        if '时序成本：' in s:
            r=fields(s,'时序成本：');n,z=int(r['轮次']),int(r['Zone'])
            rows.setdefault(n,[]).append(dict(z=z,v=int(r['有效']),cap=int(r['容量']),safe=int(r['安全']),
                retained=int(r['预计留存']),p=int(r['概率十亿分数']),credit=int(r['RR复制']),space=int(r['RR空间'])))
        if '时序方案需求：' in s:
            r=fields(s,'时序方案需求：');need[int(r['轮次'])]=demand(*(int(r[k]) for k in ('空闲','非空闲','分配等待')))
    for n,state in need.items():
        current=rows.get(n,[])
        for r in current:
            r['generation']=generations[n,r['z']]
            r['known'],r['terminal']=terminal_valid(r,history.get((n,r['z'])))
    return need,rows

def choose_v2(rows,need):
    result=choose(rows,need)
    pool=[r for r in rows if r['safe'] and 0<r['v']<r['cap'] and
        0<=r['p']<=SCALE and 0<=r['credit']<=r['v']]
    for plan in result['plans']:
        rounding=sum(int(r['retained']*r['p']%SCALE!=0) for r in pool if r['z']!=plan['first']) if result['known'] else 0
        plan['rr_rounding']=rounding;plan['score']+=rounding
    if result['known']:
        by_id={r['z']:r for r in pool}
        best=min((p for p in result['plans'] if p['base']['足够']),key=lambda p:
            (p['score'],p['base']['预计RR复制']+p['rr_rounding'],by_id[p['first']]['v'],p['base']['计划区数'],p['first']))
        result['zone']=best['first']
    return result

def replay(path):
    from analyze_joint_opportunity import require
    from analyze_deadline import audit
    lines=path.read_text().splitlines();audit(lines)
    need,rows=inputs(lines);out=[]
    for n,state in need.items():
        current=rows.get(n,[])
        old,selected,_=previous_choose(current,state);result=choose(current,state)
        out.append(dict(轮次=n,原选择=selected,边界选择=result['zone'] if result['found'] else None,
            原RR置零选择=previous_choose([dict(r,p=0,credit=0,space=0) for r in current],state)[1],
            定价完整=result['known'],空间基准=result['reference'],候选=current,方案=result['plans']))
    require(need,'没有真实时序评分，拒绝把空重放当通过')
    return out
