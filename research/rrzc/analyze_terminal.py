"""边界成本执行日志验收；旧入口必须拒绝新选区，不能混称旧模型结果。"""
from analyze_joint_opportunity import fields,require
from analyze_deadline import choose as original_choose
from replay_terminal import inputs,choose

def audit(lines,*,version=1):
    require(version in (1,2),'未知边界核版本')
    selector=choose
    if version==2:
        from replay_terminal import choose_v2 as selector
    need,rows=inputs(lines)
    tags=('边界留存','边界基准','边界方案','边界补足','边界方案决策','时序方案候选','时序方案需求')
    groups={t:{} for t in tags}
    for s in lines:
        for tag in tags:
            if tag+'：' in s:
                r=fields(s,tag+'：');groups[tag].setdefault(int(r['轮次']),[]).append(r)
    require(set(need)==set(groups['边界基准'])==set(groups['边界方案决策']),'边界需求、评分与实际决策不闭合')
    def exact(actual,expected):
        require(set(actual)==set(expected) and all(str(actual[k])==str(v) for k,v in expected.items()),'边界独立重算不一致')
    output=[]
    for n,state in need.items():
        demand_log=groups['时序方案需求'][n]
        require(len(demand_log)==1 and int(demand_log[0]['目标百分比'])==20 and
            tuple(int(demand_log[0][k]) for k in ('可知','目标字节','缺口字节'))==state,'边界空间需求被改变')
        current=rows.get(n,[]);expected=selector(current,state)
        old,original,progress=original_choose(current,state)
        for tag in ('边界基准','边界方案决策'):require(len(groups[tag][n])==1,'边界单轮记录重复')
        exact(groups['边界基准'][n][0],dict(轮次=n,完整定价=int(expected['known']),空间字节=expected['reference'],
            原选择存在=int(original is not None),原选择=original or 0))
        old_log=groups['时序方案候选'].get(n,[])
        require(len(old_log)==len(old),'原方案集合不完整')
        for a,b in zip(old_log,old):exact(a,dict(轮次=n,**b))
        retention=groups['边界留存'].get(n,[])
        require(len(retention)==len(current),'缺失候选末态留存')
        for a,b in zip(retention,current):
            exact(a,dict(轮次=n,Zone=b['z'],可知=int(b['known']),窗口纳秒=10**10,末态有效=b['terminal']))
        plans=groups['边界方案'].get(n,[]);tails=groups['边界补足'].get(n,[]);want_tails=[]
        require(len(plans)==len(expected['plans']),'边界方案集合不完整')
        for a,b in zip(plans,expected['plans']):
            values=dict(轮次=n,首区=b['first'],预计空间=b['space'],空间缺口=b['gap'],已定价=int(b['priced']),
                延期复制=b['deferred'],合计估价=b['score'])
            if version==2:values['RR取整补齐']=b['rr_rounding']
            exact(a,values)
            for z,space,copy,taken,priced in b['completion']:
                want_tails.append(dict(轮次=n,首区=b['first'],Zone=z,可用空间=space,全份复制=copy,计价空间=taken,计价复制=priced))
        require(len(tails)==len(want_tails),'边界补足集合不完整')
        for a,b in zip(tails,want_tails):exact(a,b)
        exact(groups['边界方案决策'][n][0],dict(轮次=n,实际存在=int(expected['found']),实际Zone=expected['zone'],仅进展=int(expected['progress'])))
        zero=selector([dict(r,p=0,credit=0,space=0) for r in current],state)
        output.append(dict(轮次=n,缺口字节=state[2],实际Zone=expected['zone'] if expected['found'] else None,
            仅进展=expected['progress'],方案=expected['plans'],RR项置零Zone=zero['zone'] if zero['found'] else None))
    return output
