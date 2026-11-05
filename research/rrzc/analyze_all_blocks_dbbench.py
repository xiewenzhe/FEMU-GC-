#!/usr/bin/env python3
"""真实SST配对的逐轮选区、维护与API指标独立核验；不预设改选或正收益。"""
import argparse
from collections import defaultdict
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

ZONE=128<<20
SCALE=10**9
def require(ok,why):
    if not ok:raise ValueError(why)
def fields(line,marker):
    return dict(part.split('=',1) for part in line.split(marker,1)[1].strip().split('，') if '=' in part)
def zone(value):
    address=int(value);require(address%ZONE==0,'Zone地址没有按128MiB对齐');return address//ZONE

def verify_plans(frame):
    safe={z:r for z,r in frame['时序成本'].items() if r['安全']=='1'}
    need=frame['需求'];plans=frame['计划']
    if not need or not safe:return
    constraint=frame.get('触发约束')
    if constraint is not None:
        require(constraint['GC已触发']=='1' and int(constraint['原始缺口字节'])==int(need['缺口字节']),
            '触发后约束与原始空间需求不符')
        expected=1 if need.get('可知')=='1' and int(need['缺口字节'])==0 else int(need['缺口字节'])
        require(int(constraint['评估需求字节'])==expected and int(constraint['零缺口仍选区'])==
            int(need.get('可知')=='1' and int(need['缺口字节'])==0),'触发后评估需求不符')
        need=dict(need,缺口字节=str(expected))
    if need.get('可知')=='1' and int(need['缺口字节'])==0:
        require(not plans and frame['决策']['实际存在']=='0','空间需求为零时不应构造选区方案');return
    order=sorted(safe,key=lambda z:(Fraction(int(safe[z]['有效']),int(safe[z]['容量'])-int(safe[z]['有效'])),int(safe[z]['有效']),z))
    require(set(plans)==set(safe),'模型没有给每个安全候选构造首区方案')
    for first in order:
        rows=[first];reclaim=int(safe[first]['容量'])-int(safe[first]['有效']);copy=int(safe[first]['有效'])
        for z in order:
            if reclaim>=int(need['缺口字节']):break
            if z==first:continue
            r=safe[z];rows.append(z);reclaim+=int(r['容量'])-int(r['有效'])
            copy+=(int(r['有效'])*(SCALE-int(r['概率十亿分数']))+SCALE-1)//SCALE
        rr=sum(int(r['RR复制']) for z,r in safe.items() if z!=first)
        space=sum(int(r['RR空间']) for z,r in safe.items() if z!=first)
        for r in safe.values():
            require(int(r['RR复制'])==int(r['预计留存'])*int(r['概率十亿分数'])//SCALE,'RR概率与留存复制账目不一致')
        p=plans[first]
        require([int(v) for v in p['计划区'].split('/')]==rows,'补充区规划顺序不符')
        for k,v in (('预计ZC复制',copy),('静态回收',reclaim),('预计RR复制',rr),('预计RR空间',space),('预计总复制',copy+rr)):
            require(int(p[k])==v,'独立成本重算不符：'+k)
    sufficient=[z for z,p in plans.items() if p['足够']=='1']
    if sufficient:
        key=lambda z:(int(plans[z]['预计总复制']),int(plans[z]['预计RR复制']),int(safe[z]['有效']),
            int(plans[z]['计划区数']),-(int(plans[z]['静态回收'])+int(plans[z]['预计RR空间'])),z)
        best=min(sufficient,key=key)
        require(frame['决策']['实际存在']=='1' and int(frame['决策']['实际Zone'])==best,'实际选择不符合独立成本排序')

def verify_unit_cost(frame,mode):
    """从候选原始字节独立重算分数，不把无量纲评分伪称预测节省字节。"""
    safe={z:r for z,r in frame['时序成本'].items() if r['安全']=='1' and
        0<int(r['有效'])<int(r['容量'])}
    rows=frame['单位成本'];require(set(rows)==set(safe),'单位回收候选覆盖不同')
    require(not frame['计划'],'单位回收模型仍生成补充区计划')
    priced=frame.get('空间定价',{})
    if priced:
        require(set(priced)==set(safe),'空间定价没有覆盖共同安全候选')
        reference=min(safe,key=lambda z:(Fraction(int(safe[z]['有效']),int(safe[z]['容量'])-int(safe[z]['有效'])),int(safe[z]['有效']),z))
        price_copy=int(safe[reference]['有效']);price_space=int(safe[reference]['容量'])-price_copy
    for z,c in safe.items():
        v=int(c['有效']);credit=int(c['RR复制']) if c['风险可知']=='1' else 0
        if priced:
            q=priced[z];need=frame['需求'];full=need['可知']!='1' or int(need['分配等待'])>0
            rr_space=int(c['RR空间']);space=rr_space if full else min(rr_space,int(need['缺口字节']))
            debit=min(credit,(space*price_copy+price_space-1)//price_space)
            for k,n in [('原RR抵扣',credit),('预计RR空间',rr_space),('替代空间',space),('空间价格复制',price_copy),
                        ('空间价格回收',price_space),('空间扣款',debit),('有效RR抵扣',credit-debit),('全额空间',int(full))]:
                require(int(q[k])==n,'RR空间替代账目不符：'+k)
            credit-=debit
        r=rows[z]
        for k,n in [('有效',v),('回收',int(c['容量'])-v),('RR抵扣',credit),('净复制',v-credit)]:
            require(int(r[k])==n,'单位回收账目不符：'+k)
    best=min(rows,key=lambda z:(Fraction(int(rows[z]['净复制']),int(rows[z]['回收'])),
        int(rows[z]['有效']),z)) if rows else None
    proposed=frame['单位建议'];require(proposed is not None,'单位回收建议缺失')
    require((proposed['存在']=='1')==(best is not None) and
        (best is None or int(proposed['Zone'])==best),'单位回收建议不符合独立排序')
    if mode=='blocks':
        decision=frame['决策']
        require((decision['实际存在']=='1')==(best is not None) and
            (best is None or int(decision['实际Zone'])==best),'实际执行未遵循单位回收评分')

def selections(lines,phase,mode):
    frames=defaultdict(lambda:dict(观测={},成本={},时序成本={},计划={},风险={},紧迫块明细={},需求=None,决策=None,行=None,单位成本={},单位建议=None,空间定价={}))
    block_group=None;block_buffer={}
    markers=(('V1影子候选：','观测'),('统一成本：','成本'),('时序成本：','时序成本'),
        ('时序方案候选：','计划'),('全块风险代表：','风险'),('单位回收成本：','单位成本'),('RR空间替代成本：','空间定价'))
    for i,line in enumerate(lines):
        if '全块风险明细：' in line:
            r=fields(line,'全块风险明细：');group=(int(r['轮次']),int(r['Zone']))
            if group!=block_group:block_group=group;block_buffer={}
            block_buffer[int(r['块'])]=r
        for marker,kind in markers:
            if marker not in line:continue
            r=fields(line,marker);n=int(r['轮次']);z=int(r['首区'] if kind=='计划' else r['Zone'])
            require(z not in frames[n][kind],'同轮同类候选记录重复');frames[n][kind][z]=r
            if kind=='风险' and block_group==(n,z):frames[n]['紧迫块明细'][z]=block_buffer.get(int(r['代表块']),{})
        if '时序方案需求：' in line:
            r=fields(line,'时序方案需求：');frames[int(r['轮次'])]['需求']=r
        if '单位回收建议：' in line:
            r=fields(line,'单位回收建议：');f=frames[int(r['轮次'])]
            require(f['单位建议'] is None,'单位回收建议重复');f['单位建议']=r
        if '触发后选区约束：' in line:
            r=fields(line,'触发后选区约束：');f=frames[int(r['轮次'])]
            require('触发约束' not in f,'同轮触发后约束记录重复');f['触发约束']=r
        for marker in ('统一决策：','时序方案决策：'):
            if marker in line:
                r=fields(line,marker);f=frames[int(r['轮次'])]
                require(f['决策'] is None,'同轮出现多个最终决策');f['决策']=r;f['行']=i
    result=[];all_rows=[]
    ordered=sorted((f['行'],n,f) for n,f in frames.items() if f['决策'] is not None)
    for index,(line_index,n,f) in enumerate(ordered):
        safe=sorted(z for z,r in f['成本'].items() if r['安全']=='1')
        actual=int(f['决策']['实际Zone']) if f['决策']['实际存在']=='1' else None
        greedy=min(safe,key=lambda z:(int(f['成本'][z]['有效']),z)) if safe else None
        if actual is not None:require(actual in safe,'实际Zone不属于共同安全候选')
        unit=f['单位建议'] is not None
        if unit:verify_unit_cost(f,mode)
        if mode=='greedy':require(actual==greedy,'Greedy组没有选择最少有效量候选')
        elif f['需求'] is not None and not unit:verify_plans(f)
        switched=actual is not None and greedy is not None and actual!=greedy
        greedy_plan=f['计划'].get(greedy);chosen_plan=f['计划'].get(actual)
        reason='与同轮Greedy一致' if actual is not None else '没有实际选区'
        positive=[z for z,r in f['时序成本'].items() if z in safe and int(r['RR复制'])>0]
        if mode=='blocks' and actual==greedy and actual is not None:
            reason='计入RR成本后，Greedy方案仍最优' if positive else '本轮没有非零RR复制成本估计，未改变空间成本排序'
        if actual is None and f['需求'] is not None and int(f['需求']['缺口字节'])==0:
            reason='当前模型空闲目标已满足，本轮未选择'
        if switched:
            if unit:reason='单位实际回收空间的净复制评分更低，或评分同分按有效量及Zone编号选择'
            else:
                require(greedy_plan is not None and chosen_plan is not None,'改选缺少两种方案成本')
                reason='预计总维护复制更低' if int(chosen_plan['预计总复制'])<int(greedy_plan['预计总复制']) else '总复制同分，按RR复制等次级规则选择'
        until=ordered[index+1][0] if index+1<len(ordered) else len(lines)
        tail=lines[line_index+1:until];copies=[];copy_positions=[];commits=defaultdict(list);resets=[];executions=[];zc_stats=[]
        for position,line in enumerate(tail):
            if 'V1后续迁移复制：' in line:
                r=fields(line,'V1后续迁移复制：')
                if actual is not None and int(r['源Zone'])==actual and r['复制成功']=='1':copies.append(r);copy_positions.append(position)
            if 'V1后续映射提交：' in line:
                r=fields(line,'V1后续映射提交：')
                if r['成功']=='1':commits[r['文件编号']].append(position)
            if 'Zone 重置诊断：' in line:
                r=fields(line,'Zone 重置诊断：')
                if actual is not None and int(r['Zone'])==actual and r['状态']=='成功':resets.append(position)
            if '独立约束维护结果：' in line:
                r=fields(line,'独立约束维护结果：')
                if actual is not None and int(r['轮次'])==n and int(r['实际Zone'])==actual and r['迁移成功']=='1' and \
                    int(r.get('本轮迁移字节','0'))>0 and int(r.get('本轮重置区数','0'))>0:executions.append(position)
            if 'ZC 统计：' in line:zc_stats.append(position)
        complete=False
        for reset in resets:
            ordered_copy=bool(copies) and all(any(p<c<reset for c in commits[r['文件编号']]) for r,p in zip(copies,copy_positions))
            if ordered_copy and any(reset<e<s for e in executions for s in zc_stats):complete=True
        record=dict(阶段=phase,轮次=n,快照候选Zone=[zone(z) for z in sorted(f['观测'])],
            成本比较Zone=[zone(z) for z in sorted(f['成本'])],安全可选Zone=[zone(z) for z in safe],
            GreedyZone=zone(greedy) if greedy is not None else None,实际Zone=zone(actual) if actual is not None else None,
            发生改选=switched,原因=reason,Greedy方案=greedy_plan,实际方案=chosen_plan,
            有非零RR成本的安全Zone=[zone(z) for z in positive],空间需求=f['需求'],
            选择时额外有效迁移字节=int(f['成本'][actual]['有效'])-int(f['成本'][greedy]['有效']) if switched else 0,
            预计相对Greedy减少复制字节=None if unit else int(greedy_plan['预计总复制'])-int(chosen_plan['预计总复制']) if switched else 0,
            ZC复制提交Reset闭环=complete,成功复制文件=[dict(文件编号=r['文件编号'],文件名=r['文件名'],有效字节=int(r['有效字节'])) for r in copies],
            决策日志行=line_index+1)
        if '触发约束' in f:record['触发后约束']=f['触发约束']
        if unit:record['单位回收评分']=dict(Greedy=f['单位成本'].get(greedy),实际=f['单位成本'].get(actual),
            模型建议=f['单位建议'],空间替代账目=f['空间定价'],口径='净复制/实际回收；非总复制节省预测')
        result.append(record)
        for z in sorted(set(f['观测'])|set(f['成本'])):
            raw=f['观测'].get(z,{});cost=f['时序成本'].get(z,f['成本'].get(z,{}));risk=f['风险'].get(z,{})
            detail=f['紧迫块明细'].get(z,{})
            plan=f['计划'].get(z,{})
            all_rows.append(dict(阶段=phase,轮次=n,Zone=zone(z),Zone字节地址=z,参与成本比较=z in f['成本'],
                安全可选=z in safe,同轮Greedy=z==greedy,实际选中=z==actual,
                有效字节=cost.get('有效',raw.get('有效字节','')),回收字节=f['成本'].get(z,{}).get('回收',''),
                最大块RC=raw.get('当前RC',''),设备代际=raw.get('设备代际',''),统计准入=raw.get('统计准入',''),
                最大块十秒真实增量=raw.get('十秒真实增量',''),最大块三十秒真实增量=raw.get('三十秒真实增量',''),
                紧迫块编号=risk.get('代表块',''),紧迫块RC=risk.get('代表RC',''),ETA纳秒=cost.get('ETA',''),
                紧迫块十秒真实增量=detail.get('十秒增量',''),紧迫块三十秒真实增量=detail.get('三十秒增量',''),
                紧迫块短窗真实增量=detail.get('近期增量',''),紧迫块短窗间隔纳秒=detail.get('近期间隔',''),
                风险可知=cost.get('风险可知',''),需求概率十亿分数=cost.get('概率十亿分数',''),
                RR预计复制字节=cost.get('RR复制',cost.get('RR抵扣','')),预计留存字节=cost.get('预计留存',''),
                首区方案预计总复制字节=plan.get('预计总复制',''),原始共同安全检查=f['成本'].get(z,{})))
    return result,all_rows

def histograms(text):
    rows=re.findall(r'RRZC调用延迟：操作=(READ|WRITE)，次数=(\d+)，平均纳秒=([\d.]+)，P99纳秒=(\d+)，P99.9纳秒=(\d+)，P99.99纳秒=(\d+)',text)
    require(len(rows)==2 and {r[0] for r in rows}=={'READ','WRITE'},'真实Get/Put延迟缺失')
    return {r[0]:dict(次数=int(r[1]),平均微秒=float(r[2])/1000,P99微秒=int(r[3])/1000,
        **{'P99.9微秒':int(r[4])/1000,'P99.99微秒':int(r[5])/1000}) for r in rows}

def batches(text):
    rows=re.findall(r'RRZC批次完成：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)，开始纳秒=(\d+)，完成纳秒=(\d+)',text)
    require(len(rows)==48,'实际业务批次数不是48条')
    result=[]
    for role,batch,count,base,checksum,begin,end in sorted(rows,key=lambda r:(r[0],int(r[1]))):
        i=int(batch);require(0<=i<24 and int(count)==(i+1)*(32768 if role=='READ' else 24576),'实际累计操作不符')
        require(int(base)==(i//4)*65536 and int(end)>=int(begin),'热点或批次时间不符')
        result.append([role,i,int(count),int(base),int(checksum)])
    require(len({(r[0],r[1]) for r in result})==48,'批次记录重复')
    return result

def maintenance(text):
    zc=[int(v) for v in re.findall(r'ZC 统计：轮次 \d+，本轮迁移 (\d+) 字节',text)]
    rr=[int(v) for v in re.findall(r'RR 统计：尝试 \d+ 次，本次迁移 (\d+) 字节',text)]
    return dict(ZC次数=len(zc),RR次数=len(rr),ZC迁移字节=sum(zc),RR迁移字节=sum(rr),总迁移字节=sum(zc)+sum(rr),
        全部成功Reset次数=len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功',text)))

def layout_space(root):
    result=json.loads((root/'space.business.json').read_text());rows=result['Zone']
    require(len(rows)>0 and sum(int(z['剩余']) for z in rows)==int(result['空闲字节']), '只读空间与逐区剩余不一致')
    require(sum(int(z['有效']) for z in rows)==int(result['有效字节']),'逐区有效空间不一致')
    result['已确认']=True;result['来源']='业务进程结束后只读挂载ZenFS，非跨层原子快照'
    return result

def arm(root,mode,load_root=None,records=1048576,all_blocks_observation=False):
    require((root/'wrapper.exit').read_text().strip()=='0','实验臂退出失败')
    native=(root/'run.log').read_text();latency=histograms(native);trace=batches(native)
    require(latency['READ']['次数']==786432 and latency['WRITE']['次数']==589824,'读写总量不符')
    verify=(root/'verify.log').read_text()
    require(f'{records} operations;' in verify and 'verifychecksum' in verify,'全键迭代或CRC核验缺失')
    before=json.loads((root/'strict.before.json').read_text());after=json.loads((root/'strict.business.json').read_text())
    if (root/'真实准备读取.json').exists():
        # 准备读属于起点构造，不能混入业务期设备计数；保留原始快照供追溯。
        before=json.loads((root/'真实准备读取.json').read_text())['设备后']
    require(after['历史最高RC']<=1024 and after['启用']==1,'严格RR配置或峰值异常')
    run_text=(root/'run.zenfs.log').read_text()
    load=((load_root if load_root is not None else root)/'load.zenfs.log').read_text()
    require('全块查询失败：' not in run_text and '状态=失败' not in run_text and '[ERROR]' not in run_text,'业务维护错误')
    require(f'全块风险配置：启用={int(mode=="blocks" or all_blocks_observation)}' in run_text,'全块观察或模型实际开关不符')
    rounds=[];candidates=[]
    for phase,text in (('装载',load),('业务',run_text)):
        r,c=selections(text.splitlines(),phase,mode);rounds+=r;candidates+=c
    seconds=(int((root/'run.end.ns').read_text())-int((root/'run.start.ns').read_text()))/1e9
    require(seconds>0,'阶段耗时无效')
    return dict(通过=True,业务维护=maintenance(run_text),装载维护=maintenance(load),API延迟=latency,
        阶段秒=seconds,阶段总吞吐ops=(786432+589824)/seconds,批次请求摘要=trace,
        实际阈值事件差=after['阈值触发事件']-before['阈值触发事件'],
        选区轮次=rounds,候选明细=candidates,改选决策次数=sum(r['发生改选'] for r in rounds),
        改选后ZC闭环次数=sum(r['发生改选'] and r['ZC复制提交Reset闭环'] for r in rounds),
        多候选决策帧数=sum(len(r['安全可选Zone'])>=2 for r in rounds),
        有非零RR估计的决策帧数=sum(bool(r['有非零RR成本的安全Zone']) for r in rounds),
        初始空间=json.loads((root/'space.before.json').read_text()),结束空间=layout_space(root))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('guest_root',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result={mode:arm(a.guest_root/mode,mode) for mode in ('greedy','blocks')}
    require(result['greedy']['批次请求摘要']==result['blocks']['批次请求摘要'],'配对实际键序列不同')
    require((a.guest_root/'greedy/公共参数.txt').read_bytes()==(a.guest_root/'blocks/公共参数.txt').read_bytes(),'公共配置不一致')
    a.output.mkdir(exist_ok=True)
    with (a.output/'独立核验.json').open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    for mode,r in result.items():
        for name,rows in (('逐轮选区',r['选区轮次']),('逐区候选',r['候选明细']),('改选明细',[x for x in r['选区轮次'] if x['发生改选']])):
            with (a.output/(mode+'-'+name+'.csv')).open('x') as f:
                keys=list(rows[0]) if rows else ['阶段','轮次','安全可选Zone','GreedyZone','实际Zone','发生改选','原因']
                writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
                for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    with (a.output/'API延迟.csv').open('x') as f:
        writer=csv.DictWriter(f,fieldnames=['策略','操作','次数','平均微秒','P99微秒','P99.9微秒','P99.99微秒']);writer.writeheader()
        for mode,r in result.items():
            for op,data in r['API延迟'].items():writer.writerow(dict(策略=mode,操作=op,**data))
    manifest={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for mode in result for q in (a.guest_root/mode).iterdir() if q.is_file()}
    with (a.output/'分析来源.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    lines=['# 真实SST全块模型配对','',
        '8GiB、RR1024、GC20%、无WAL、未锁页；定制db_bench轮转负载，非公开标准，单轮非稳态。',
        '吞吐按固定总读写数除完整业务阶段耗时，包含布局采样、flush及等待压缩；Get/Put延迟单独计时。',
        'Greedy反事实只在同一轮、同一安全候选中重算，不能冒充另一次独立Greedy执行的后续结果。','',
        '| 策略 | 业务ZC | 业务RR | 业务迁移MiB | 全部成功Reset | 阶段吞吐ops/s | 改选决策 | 改选闭环 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode,r in result.items():
        m=r['业务维护'];lines.append(f"| {mode} | {m['ZC次数']} | {m['RR次数']} | {m['总迁移字节']/(1<<20):.3f} | {m['全部成功Reset次数']} | {r['阶段总吞吐ops']:.2f} | {r['改选决策次数']} | {r['改选后ZC闭环次数']} |")
    lines+=['','| 策略 | 操作 | 平均μs | P99μs | P99.9μs | P99.99μs |','|---|---|---:|---:|---:|---:|']
    for mode,r in result.items():
        for op,h in r['API延迟'].items():
            lines.append(f"| {mode} | {op} | {h['平均微秒']:.3f} | {h['P99微秒']:.3f} | {h['P99.9微秒']:.3f} | {h['P99.99微秒']:.3f} |")
    lines+=['','| 策略 | 装载后空闲MiB | 结束空闲MiB | 结束有效MiB | 结束满区可回收MiB |','|---|---:|---:|---:|---:|']
    for mode,r in result.items():
        s=r['结束空间'];lines.append(f"| {mode} | {int(r['初始空间']['空闲字节'])/(1<<20):.3f} | {int(s['空闲字节'])/(1<<20):.3f} | {int(s['有效字节'])/(1<<20):.3f} | {int(s['满区可回收字节'])/(1<<20):.3f} |")
    lines+=['','本轮实际改选（候选为同轮通过共同安全检查的Zone）：','',
        '| 阶段／轮次 | 安全可选Zone | Greedy | 实际 | 原因 | 已完成ZC闭环 |','|---|---|---:|---:|---|---|']
    changed=[r for r in result['blocks']['选区轮次'] if r['发生改选']]
    for r in changed:lines.append(f"| {r['阶段']}/{r['轮次']} | {r['安全可选Zone']} | {r['GreedyZone']} | {r['实际Zone']} | {r['原因']} | {r['ZC复制提交Reset闭环']} |")
    if not changed:lines+=['','本轮没有发生相对同轮Greedy的实际改选，不以启用模型替代改选证据。']
    lines+=['','逐轮选区CSV保存全部观测候选、成本候选、安全可选候选、Greedy建议与实际选择；逐区CSV保存每个Zone状态，改选CSV只列差异轮次。',
        'Zone编号从0开始，字节地址除以128MiB。成功闭环要求观察到同轮同区维护成功结果、复制、映射提交、源Reset及ZC统计；不足时不冒称已完成。',
        '需要同时核对结束可用空间、有效量与剩余可回收量，再解释总迁移差异；缺少确认时不宣称等终态净收益。','']
    with (a.output/'配对分析.md').open('x') as f:f.write('\n'.join(lines))
    print(json.dumps({m:{k:r[k] for k in ('业务维护','阶段总吞吐ops','改选决策次数','改选后ZC闭环次数')} for m,r in result.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
