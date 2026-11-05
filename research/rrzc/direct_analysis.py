"""统一模型独立整数重算及长尾/擦除核验；不把逻辑操作当设备IOPS。"""
import csv
from fractions import Fraction
import json
import math
from pathlib import Path
import re
from analyze_v1_shadow_stream import fields,require,MAX,SECOND
from analyze_v1_growth_stream import qualifies


def estimate(c,b,f):
    v=int(c['有效字节']);out=dict(风险可知=0,ETA=MAX,预计留存=0,RR抵扣=0,净复制=v,回收=int(c['容量字节'])-v)
    if not b or b.get('统计准入')!='1':return out
    rc,flags,d10=(int(b[k]) for k in ('当前RC','窗口标志','十秒真实增量'))
    if not flags&1 or flags&8:return out
    if rc>=4096:out.update(风险可知=1,ETA=0)
    elif flags&2:out.update(风险可知=1,ETA=min(MAX,((4096-rc)*10*SECOND+d10-1)//d10) if d10 else MAX)
    t=out['ETA']
    if not out['风险可知'] or t>10*SECOND:return out
    if t==0:kept=v
    else:
        if f['可知']!='1':return out
        a,d,l10,l30=(int(f[k]) for k in ('十秒间隔','三十秒间隔','十秒减少','三十秒减少'))
        require(10*SECOND<=a<=12*SECOND and 30*SECOND<=d<=32*SECOND and l10<=l30,'统一留存输入窗口非法')
        kept=v-min(v,max((l10*t+a-1)//a,(l30*t+d-1)//d))
    out.update(预计留存=kept,RR抵扣=kept,净复制=v-kept);return out


def parse_direct(lines):
    candidates=[];blocks={};forecasts={};rows=[];source={};spares={};decisions=[];files=[];maintenance=[];last=None
    for number,s in enumerate(lines,1):
        if '简单双因素候选：' in s:candidates.append(fields(s,'简单双因素候选：'))
        if 'V1影子候选：' in s:
            b=fields(s,'V1影子候选：');z=int(b['Zone']);require(z not in blocks,'同块候选重复');blocks[z]=b
            if '当前RC' in b:
                rc,x,y,flags=(int(b[k]) for k in ('当前RC','十秒真实增量','三十秒真实增量','窗口标志'))
                require(0<=x<=y<=rc and flags&1 and not flags&~15,'同块窗口计数非法')
                ch,lun,pl,blk=map(int,b['PPA'].split('/'))
                require(0<=ch<8 and 0<=lun<4 and 0<=pl<2 and blk==z//(256<<20) and
                    int(b['块编号'])==(ch*4+lun)*2+pl,'统一候选块身份非法')
                age=int(b['快照纳秒'])-int(b['代际起点纳秒'])
                require(age>=0 and (not flags&2 or age>=10*SECOND) and (not flags&4 or age>=30*SECOND),'窗口未预热')
        if 'V1影子文件：' in s:files.append(fields(s,'V1影子文件：'))
        if '统一留存：' in s:
            f=fields(s,'统一留存：');z=int(f['Zone']);require(z not in forecasts,'统一留存重复');forecasts[z]=f
        if '统一源复核：' in s:
            r=fields(s,'统一源复核：');z=int(r['Zone']);require(z not in source,'源复核重复')
            stable=[int(n) for n in r['稳定'].split('/')]
            ok=(0<=int(r['经过纳秒'])<=2*SECOND and r['原写指针']==r['当前写指针'] and
                r['原有效']==r['当前有效'] and stable==[1,1,1] and
                len(set(r['代际'].split('/')))==len(set(r['序号'].split('/')))==1)
            require(r['通过']==str(int(ok)),'统一源复核重算不符');source[z]=ok
        if '统一成本：' in s:rows.append(fields(s,'统一成本：'))
        if '统一替补复核：' in s:
            r=fields(s,'统一替补复核：');z=int(r['Zone'])
            require(z not in spares,'替补复核重复');spares[z]=r
        if '统一决策：' in s:
            d=fields(s,'统一决策：');rid=str(len(decisions)+1)
            require(d['轮次']==rid and int(d['界限纳秒'])==10*SECOND and int(d['回收资格'])==192<<20 and
                d['先选Greedy']=='0' and int(d['候选数'])==len(candidates),'统一决策入口或轮次不符')
            cs={int(c['Zone']):c for c in candidates};eligible={z:c for z,c in cs.items() if qualifies(c)}
            require(len(cs)==len(candidates) and set(cs)==set(blocks)==set(forecasts),'统一输入集合不完整')
            require(all(r['轮次']==rid for r in list(blocks.values())+list(forecasts.values())+rows),'统一输入跨轮次')
            require(len(rows)==len(eligible)==int(d['排序数']) and {int(r['Zone']) for r in rows}==set(eligible),'统一评分集合不符')
            ranked=[]
            for r in rows:
                z=int(r['Zone']);c=cs[z];cost=estimate(c,blocks[z],forecasts[z])
                require(all(int(r[k])==v for k,v in cost.items()) and r['有效']==c['有效字节'],'统一成本独立重算不符')
                if r['预留稳定']=='1':
                    require(int(r['物理上界'])<=int(r['预留剩余']) or
                        int(r['预留有效'])==0 and int(r['物理上界'])<=int(c['容量字节']),'统一预留容量不足却放行')
                spare_ok=False
                if '替补可用' in r:
                    require(z in spares and spares[z]['轮次']==rid,'缺少本轮原始替补复核')
                    spare_ok=spare_fits(spares[z],int(r['物理上界']),r['覆盖']=='1')
                    require(r['替补可用']==spares[z]['通过']==str(int(spare_ok)),'替补资格独立重算不符')
                destination=r['覆盖']=='1' and (r['预留稳定']=='1' or spare_ok or int(r['空目标'])>=int(r['物理上界']))
                safe=source.get(z,False) and destination
                require(r['源稳定']==str(int(source.get(z,False))) and r['安全']==str(int(safe)),'统一安全复核不符')
                ranked.append((Fraction(cost['净复制'],cost['回收']),int(c['有效字节']),z,safe))
            require([r[2] for r in sorted(ranked)]==[int(r['Zone']) for r in rows],'统一排序不符')
            chosen=next((r[2] for r in sorted(ranked) if r[3]),0)
            require(d['实际存在']==str(int(bool(chosen))) and int(d['实际Zone'])==chosen,'统一实际选区不符')
            # Greedy只在决策之后作为诊断参照，不参与模型或执行准入。
            greedy=min(eligible,key=lambda z:(int(cs[z]['有效字节']),z)) if eligible else 0
            decisions.append(dict(d,行号=number,独立核验改选=bool(chosen and chosen!=greedy),
                空间最少参照=greedy,预算不是策略输入=True))
            require(not spares or set(spares)==set(eligible),'替补复核集合不符')
            last=decisions[-1];candidates=[];blocks={};forecasts={};rows=[];source={};spares={}
        if '简单双因素实际集合：' in s:
            a=fields(s,'简单双因素实际集合：')
            if last is not None:
                require(a['实际选中']==last['实际存在'] and
                    (last['实际存在']=='0' or a['Zone']==last['实际Zone']),'统一实际集合不符');last=None
        if '独立约束维护结果：' in s:maintenance.append(fields(s,'独立约束维护结果：'))
    require(decisions and not candidates and not rows and last is None,'统一决策未闭合')
    chosen=[d for d in decisions if d['实际存在']=='1']
    require(len(chosen)==len(maintenance),'统一维护记录缺失')
    for d,r in zip(chosen,maintenance):
        require(all(d[k]==r[k] for k in ('轮次','实际Zone')) and r['迁移成功']=='1' and r['本轮重置区数']=='1','统一维护失败或未闭合')
    return decisions,files,maintenance


def spare_fits(r,upper,covered):
    pairs={k:[int(v) for v in r[k].split('/')] for k in
        ('启用','可知','存在','替补Zone','剩余','有效','写指针','代际','序号')}
    require(all(len(v)==2 and all(n>=0 for n in v) for v in pairs.values()),'替补原始观察字段非法')
    return (covered and int(r['容量'])>0 and all(pairs[k]==[1,1] for k in ('启用','可知','存在')) and
        all(a==b for a,b in pairs.values()) and int(r['Zone'])!=pairs['替补Zone'][0] and
        pairs['有效']==[0,0] and pairs['写指针']==pairs['替补Zone'] and
        pairs['剩余']==[int(r['容量'])]*2 and upper<=pairs['剩余'][0])


def verify_smoke(root):
    for n in ('run.exit','verify.exit','runner.exit','wrapper.exit'):
        require((root/n).read_text().strip()=='0','统一功能阶段失败：'+n)
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('统一选区配置：启用=1，' in raw and '[ERROR]' not in raw,'统一入口未启用或出错')
    require('独立约束决策：' not in raw,'统一入口仍运行旧建议执行器')
    from replay_space_emergency import replay as space
    from replay_single_recovery import replay as single
    lines,_=space(lines);lines,_=single(lines)
    ds,_,ms=parse_direct(lines)
    release=next(fields(s.split('，后续固定',1)[0],'收益预算功能释放：') for s in lines if '收益预算功能释放：' in s)
    require(any(d['实际Zone']==release['热Zone'] for d in ds),'统一模型没有选择热区')
    require('只读校验通过：完整56MiB有效数据，24个填充文件首尾4KiB' in (root/'verify.stdout.log').read_text(),'完整载荷校验失败')
    require((root/'software.before.log').read_bytes()==(root/'software.after.log').read_bytes(),'运行软件变化')
    return dict(验收通过=True,决策=ds,维护=ms,边界='真实ZNS文件功能测试，不是YCSB或RR节省结论')


def verify_spare_smoke(root):
    for n in ('run.exit','verify.exit','runner.exit','wrapper.exit'):
        require((root/n).read_text().strip()=='0','替补回归阶段失败：'+n)
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('统一选区配置：启用=1，' in raw and '[ERROR]' not in raw,'替补回归入口未启用或出错')
    from replay_space_emergency import replay as space
    from replay_single_recovery import replay as single
    replayed,_=space(lines);replayed,_=single(replayed)
    ds,_,ms=parse_direct(replayed)
    ready=next(fields(s,'替补回归准备：') for s in lines if '替补回归准备：' in s)
    require(ready['普通空区']=='0' and int(ready['当前尾部'])==8<<20 and
        int(ready['当前有效'])==248<<20 and int(ready['有效字节'])==32<<20 and
        int(ready['替补容量'])==256<<20,'未形成真实失败前提')
    rows=[fields(s,'统一成本：') for s in lines if '统一成本：' in s]
    require(any(r['Zone']==ready['源Zone'] and r['空目标']=='0' and r['预留稳定']=='0' and
        r['替补可用']=='1' and r['安全']=='1' for r in rows),'没有仅依靠真实替补放行')
    done=next(fields(s,'替补回归完成：') for s in lines if '替补回归完成：' in s)
    require(done['源Zone']==ready['源Zone'] and done['实际目标']==ready['替补Zone'] and done['源已Reset']=='1','目标接续或源Reset不符')
    require('只读校验通过：完整280MiB有效数据，26个填充文件首尾4KiB' in (root/'verify.stdout.log').read_text(),'完整回归数据验证失败')
    require((root/'software.before.log').read_bytes()==(root/'software.after.log').read_bytes(),'回归软件变化')
    return dict(验收通过=True,准备=ready,完成=done,决策=ds,维护=ms,
        边界='真实文件及原GC形成的目标接续回归，不是YCSB性能或模型收益结论')


def extended_latency(text,expected):
    lines=[s for s in text.splitlines() if ' sec: ' in s and 'operations;' in s]
    require(lines,'缺少真实状态');out={}
    keys=('Count','Avg','99','99.9','99.99')
    for op,body in re.findall(r'\[([^:]+):([^\]]+)\]',lines[-1]):
        pairs=re.findall(r'(?:^|\s)(Count|Avg|99\.99|99\.9|99)=([\d.]+)(?=\s|$)',body)
        require(len(pairs)==len(keys) and len(dict(pairs))==len(keys),'高分位缺失或重复')
        r=dict(pairs);v=[float(r[k]) for k in keys]
        require(all(math.isfinite(n) and n>=0 for n in v) and v[2]<=v[3]<=v[4],'高分位或均值非法')
        out[op]=dict(次数=int(r['Count']),平均微秒=v[1],p99微秒=v[2],p999微秒=v[3],p9999微秒=v[4])
    require(set(out)=={'READ','UPDATE'} and sum(r['次数'] for r in out.values())==expected,'调用数或操作类型不符')
    return out


def erases(first,last):
    require(all(first[k]==last[k] for k in ('会话高','会话低','块数每区')) and first['块数每区']==64,'设备会话或几何变化')
    a,b=first['代际'],last['代际'];require(len(a)==len(b)==32 and all(y>=x>=0 for x,y in zip(a,b)),'设备代际回退')
    n=sum(y-x for x,y in zip(a,b))
    return dict(设备Zone擦除次数=n,模拟NANDBlock擦除次数=n*64,
        口径='由FEMU C2代际差实测Reset次数；本8通道×4LUN×2plane映射每次Zone Reset执行64次模拟Block擦除，包含非RR/ZC重置，不是硬件实测。')


def audit_run(root,mode,health=False):
    from analyze_ycsb_retention import audit
    require(mode in ('greedy','direct'),'统一对照模式非法')
    r=audit(root,mode,nowal=True,expected_ops=1200000,single_recovery=True,space_emergency=True,
        independent_ratio='50/50',update_range=(4096,258048),direct_model=mode=='direct')
    raw=(root/'zenfs.run.log').read_text()
    if mode=='greedy':require('统一选区配置：' not in raw,'Greedy组意外启用统一选择器')
    r['完整调用延迟']=extended_latency((root/'ycsb.log').read_text(),1200000)
    initial,before,after=(json.loads((root/n).read_text()) for n in
        ('device.initial.json','device.before.json','device.after.json'))
    r['全程擦除含初始化']=erases(initial,after)
    r['装载及业务擦除不含格式化']=erases(before,after)
    r['正常模型选择轮数']=len(r['决策'])
    r['模型选择与空间最少不同轮数']=sum(d['独立核验改选'] for d in r['决策'])
    r['指标说明']='延迟为YCSB绑定API，不是单个NVMe命令延迟；UPDATE含Get+Put。总迁移为ZC+RR维护逻辑复制量。'
    if health:
        from rrzc_rr_health import inspect
        r['RR健康验收']=inspect(raw)
        require(r['RR健康验收']['验收通过'],'RR健康验收失败：'+str(r['RR健康验收']['错误']))
    return r


def verify_content_smoke(root):
    from rrzc_rr_health import inspect
    for n in ('run.exit','verify.exit','runner.exit','wrapper.exit'):
        require((root/n).read_text().strip()=='0','内容回归失败：'+n)
    raw=(root/'zenfs.run.log').read_text();r=inspect(raw)
    require(r['验收通过'],'内容回归RR健康验收失败：'+str(r['错误']))
    completed=[fields(s,'内容回归RR完成：') for s in raw.splitlines() if '内容回归RR完成：' in s]
    require(len(completed)==2 and [v['轮次'] for v in completed]==['1','2'],'缺少两次真实RR完成证据')
    for row in completed:
        require(row['目标Zone'] not in row['元数据Zone'].split('/') and
            int(row['源代际'].split('/')[1])==int(row['源代际'].split('/')[0])+1 and
            row['真实读取']=='4096','迁移目标或Reset证据非法')
    require('只读校验通过：完整18MiB数据，重新挂载内容类别恢复正确' in (root/'verify.stdout.log').read_text(),'完整18MiB校验/恢复类别未通过')
    require('公共内容隔离配置：启用=1，' in (root/'zenfs.verify.log').read_text(),'只读挂载未恢复内容类别')
    require((root/'software.before.log').read_bytes()==(root/'software.after.log').read_bytes(),'软件身份变化')
    return dict(验收通过=True,两次RR=completed,RR健康=r,边界='真实分配和RR功能回归，不是性能实验')


def compare(root):
    from analyze_ycsb_retention import sha
    modes=('greedy','direct');root=Path(root)
    require(len({sha(root/m/'20261017/workload.properties') for m in modes})==1,'两组配置不同')
    arms={m:json.loads((root/m/'独立核验.json').read_text()) for m in modes}
    require(all(r['验收通过'] for r in arms.values()),'存在未通过组')
    for k in ('软件清单SHA256','请求轨迹SHA256','最新值清单SHA256'):
        require(len({r[k] for r in arms.values()})==1,'两组身份不同：'+k)
    if any('RR健康验收' in r for r in arms.values()):
        require(all(r.get('RR健康验收',{}).get('验收通过') for r in arms.values()),'两组健康验收不完整或失败')
    require(arms['greedy']['实际改选次数']==0,'Greedy不应执行改选')
    keys=('ZC重置','RR重置','总迁移MiB','ZC迁移MiB','RR迁移MiB')
    return dict(配对核验通过=True,各组={m:{k:r[k] for k in (*keys,'完整调用延迟','吞吐量',
        '全程擦除含初始化','装载及业务擦除不含格式化','模型选择与空间最少不同轮数')} for m,r in arms.items()},
        RR健康验收={m:r.get('RR健康验收',dict(验收通过=False,说明='旧结果未进行维护健康验收')) for m,r in arms.items()},
        相对Greedy={k:arms['direct'][k]-arms['greedy'][k] for k in keys},
        边界='单次同请求配对不证明显著性；记录负结果；同请求不保证异步压实布局相同。直接统一排序，不以Greedy作为准入。')
