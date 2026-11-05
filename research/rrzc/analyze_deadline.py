"""时序实验的独立整数方案重算；RR和补充ZC是互斥分支。"""
from fractions import Fraction
from analyze_joint_opportunity import fields,require
from analyze_space_plan import demand
from replay_window_credit import oracle as window_oracle,MAX,SECOND
from replay_expected_rr import probability
SCALE=10**9

def choose(rows,need):
    known,_,gap=need
    if known and not gap:return [],None,False
    pool=[r for r in rows if r['safe'] and 0<r['v']<r['cap'] and 0<=r['p']<=SCALE and 0<=r['credit']<=r['v']]
    pool.sort(key=lambda r:(Fraction(r['v'],r['cap']-r['v']),r['v'],r['z']))
    plans=[]
    for first in pool:
        members=[first];reclaim=first['cap']-first['v'];copy=first['v']
        for x in pool:
            if reclaim>=gap:break
            if x is first:continue
            members.append(x);reclaim+=x['cap']-x['v'];copy+=(x['v']*(SCALE-x['p'])+SCALE-1)//SCALE
        rr=sum(x['credit'] for x in pool if x is not first)
        space=sum(x['space'] for x in pool if x is not first)
        plans.append(dict(首区=first['z'],足够=int(known and reclaim>=gap),计划区数=len(members),
            计划区='/'.join(str(x['z']) for x in members),预计ZC复制=copy,静态回收=reclaim,
            预计RR复制=rr,预计RR空间=space,预计总复制=copy+rr))
    feasible=[p for p in plans if p['足够']]
    if feasible:
        by_id={x['z']:x for x in pool}
        p=min(feasible,key=lambda p:(p['预计总复制'],p['预计RR复制'],by_id[p['首区']]['v'],p['计划区数'],
            -p['静态回收']-p['预计RR空间'],p['首区']))
        return plans,p['首区'],False
    p=min(pool,key=lambda r:(r['v']-r['cap'],Fraction(r['v'],r['cap']-r['v']),r['v'],r['z'])) if pool else None
    return plans,p['z'] if p else None,bool(p)

def audit(lines):
    tags=('时序成本','时序方案需求','时序方案候选','时序方案决策')
    groups={t:{} for t in tags}
    for s in lines:
        for t in tags:
            if t+'：' in s:
                r=fields(s.split('，原因=',1)[0],t+'：');groups[t].setdefault(int(r['轮次']),[]).append(r)
    require(set(groups['时序方案需求'])==set(groups['时序方案决策']),'时序需求与决策不闭合')
    output=[]
    for n,records in groups['时序方案需求'].items():
        require(len(records)==len(groups['时序方案决策'][n])==1,'同轮时序需求或决策重复')
        d=records[0];need=demand(*(int(d[k]) for k in ('空闲','非空闲','分配等待','目标百分比')))
        require((int(d['可知']),int(d['目标字节']),int(d['缺口字节']))==need and int(d['目标百分比'])==20,'时序需求重算不符')
        rows=[]
        for r in groups['时序成本'].get(n,[]):
            rows.append(dict(z=int(r['Zone']),v=int(r['有效']),cap=int(r['容量']),credit=int(r['RR复制']),
                p=int(r['概率十亿分数']),space=int(r['RR空间']),safe=int(r['安全'])))
        require(len({x['z'] for x in rows})==len(rows),'时序候选重复')
        plans,z,progress=choose(rows,need);logged=groups['时序方案候选'].get(n,[])
        actual={int(x['首区']):x for x in logged};require(len(actual)==len(logged)==len(plans),'时序方案数量不符')
        for p in plans:
            require(p['首区'] in actual and all((actual[p['首区']][k]==v if isinstance(v,str) else
                int(actual[p['首区']][k])==v) for k,v in p.items()),'时序方案成本重算不符')
        selected=groups['时序方案决策'][n][0]
        require((int(selected['实际存在']),int(selected['实际Zone']),int(selected['仅进展']))==
            (int(z is not None),z or 0,int(progress)),'时序实际选择不符')
        output.append(dict(轮次=n,缺口字节=need[2],实际Zone=z,仅进展=progress,方案=plans))
    return output

def forecast(values):
    base=window_oracle(values[:18]);risk,eta,_,_,_,reclaim,read_span,window=base
    z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs=values
    retained=credit=known=units=0
    if not risk:return risk,eta,retained,credit,v,reclaim,window,known,units
    if rc>=threshold:return 1,0,v,v,0,reclaim,window,1,SCALE
    if not gk or not 2*SECOND<=gs<=4*SECOND or gs>age or gd>rc or gs<=read_span and gd>d10:
        return 0,MAX,0,0,v,reclaim,window,0,0
    eta=max(eta,min(MAX,((threshold-rc)*gs+gd-1)//gd) if gd else MAX)
    if eta==MAX or not window:return risk,eta,0,0,v,reclaim,window,0,0
    retained=max(0,v-max((sl*eta+ss-1)//ss,(ll*eta+ls-1)//ls))
    known,units=probability(threshold-rc,min(Fraction(d10,read_span),Fraction(gd,gs))*window)
    credit=retained*units//SCALE
    return risk,eta,retained,credit,v-credit,reclaim,window,known,units

def audit_forecasts(lines,*,time_consistent=False):
    require(time_consistent or not any('时间一致配置：版本=1，启用=1，' in s for s in lines),
        '旧时序核验入口拒绝时间一致新版本')
    tags=('V1影子候选','窗口收益成本','时序冻结块','时序读预测','统一成本','时序成本','时间一致证据')
    maps={t:{} for t in tags};frames={}
    for s in lines:
        for t in tags:
            if t+'：' not in s:continue
            r=fields(s.split('，原因=',1)[0],t+'：');n=int(r['轮次']);z=int(r['Zone'])
            if t=='时序冻结块':
                key=n,int(r['帧']),z;require(key not in frames,'冻结原始块重复');frames[key]=r
            else:
                require((n,z) not in maps[t],'同轮时序诊断重复');maps[t][n,z]=r
    require(set(maps['时序成本'])==set(maps['统一成本']),'安全评分与时序成本集合不同')
    output=[]
    for key,p in maps['时序读预测'].items():
        n,z=key;b=maps['V1影子候选'][key];w=maps['窗口收益成本'][key]
        good=int(b['统计准入']);known=delta=span=0;previous=None
        if good:
            upper_time=int(b['快照纳秒']);upper_rc=int(b['当前RC'])
            frame_ids=sorted({k[1] for k in frames if k[0]==n},reverse=True)
            require(len(frame_ids)<=4,'超过四帧冻结历史')
            for i in frame_ids:
                old=frames.get((n,i,z));previous=old
                if old is None:break
                if old['会话']!=b['设备会话'] or old['代际']!=b['设备代际'] or old['块']!=b['块编号'] or \
                    old['PPA']!=b['PPA'] or old['起点']!=b['代际起点纳秒'] or \
                    int(old['区编号'])!=z//(128<<20) or int(old['阈值'])!=1024 or \
                    int(old['每区块数'])!=128 or not int(old['标志'])&1:break
                ts=int(old['快照']);rc=int(old['RC'])
                if ts>=upper_time or rc>upper_rc:break
                upper_time,upper_rc=ts,rc;span=int(b['快照纳秒'])-ts
                if span<2*SECOND:continue
                if span>4*SECOND:break
                known=1;delta=int(b['当前RC'])-rc;break
            # 如果未找到合法端点，Check仍记录最后检查端点及其间隔；由上面的实际规则核验。
            require(int(p['近期可知'])==known and int(p['近期增量'])==delta and int(p['近期间隔'])==span,
                '多帧同块近期证据重算不符')
            if previous is not None:
                require(all(p[a]==previous[c] for a,c in (('前快照','快照'),('前RC','RC'),('前代际','代际'),
                    ('前块','块'),('前PPA','PPA'))),'近期端点未对应实际冻结帧')
        values=(z,int(b['有效字节']),128<<20,int(b['当前RC']) if good else 0,
            int(b['十秒真实增量']) if good else 0,int(b['三十秒真实增量']) if good else 0,
            int(b['窗口标志']) if good else 0,
            int(b['快照纳秒'])-int(b['代际起点纳秒']) if good else 0,1024,int(b['设备代际']) if good else 0,
            *(int(w[k]) for k in ('留存可知','留存Zone','留存代际','留存有效','短减少','短间隔','长减少','长间隔')),
            known,delta,span)
        if time_consistent:
            from analyze_joint_time import forecast as joint_forecast
            expected=joint_forecast(values)
            clock=maps['时间一致证据'][key]
            require(tuple(int(clock[k]) for k in ('预计存活纳秒','事件窗口纳秒','条件到达纳秒'))[:2]==expected[9:11] and
                abs(int(clock['条件到达纳秒'])-expected[11])<=2,'同窗条件时间与独立恒等式不同')
        else:expected=forecast(values)
        actual=tuple(int(p[k]) for k in ('风险可知','ETA','预计留存','RR复制','净复制'))
        units=int(p['概率十亿分数'])
        require(actual[:3]==expected[:3] and int(p['窗口'])==expected[6] and int(p['概率可知'])==expected[7] and
            abs(units-expected[8])<=1 and 0<=units<=SCALE,'时序预测与独立概率计算不同')
        require(actual[3]==actual[2]*units//SCALE and actual[4]+actual[3]==values[1],
            '时序概率与复制字节不闭合')
        output.append(dict(轮次=n,Zone=z,ETA=actual[1],RR抵扣=actual[3],净复制=actual[4]))
    for key,c in maps['时序成本'].items():
        old=maps['统一成本'][key]
        require(c['安全']==old['安全'] and c['有效']==old['有效'] and int(c['容量'])==int(old['有效'])+int(old['回收']),
            '时序选区绕过公共源/目标安全')
        p=maps['时序读预测'].get(key)
        if p is None:
            require(all(int(c[k])==0 for k in ('RR复制','RR空间','概率十亿分数')),'零RR对照混入预测')
        else:
            require(all(c[a]==p[b] for a,b in (('风险可知','风险可知'),('ETA','ETA'),('预计留存','预计留存'),
                ('概率十亿分数','概率十亿分数'),('RR复制','RR复制'))),'实际方案使用了不同预测')
            require(int(c['RR空间'])==(int(c['容量'])-int(c['预计留存']))*int(c['概率十亿分数'])//SCALE,
                '预测RR回收不闭合')
    return output
