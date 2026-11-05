#!/usr/bin/env python3
"""只读回溯Greedy候选与后续维护；潜在机会不等于实际可避免RR。"""
import argparse
from collections import Counter,defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from analyze_joint_opportunity import fields,model_decisions,require

ZONE_BYTES=128<<20
DEFAULT=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-rotating-v9-20261024-results/full-hot-space')


def clock(line):
    value=datetime.strptime(line.split()[0],'%Y/%m/%d-%H:%M:%S.%f')
    return (value-datetime(1970,1,1)).total_seconds()


def follow(candidate,events):
    """遇到首次维护/Reset即截止；不能越过一次Reset去匹配复用后的RR。"""
    for event in events:
        if event['行']<=candidate['决策行']:continue
        if event['类型']=='Reset':return '先Reset',event
        if event['主机代际']!=candidate['主机代际']:return '代际无法衔接',event
        if event['类型']=='ZC':return '先后续ZC',event
        if event['类型']=='RR':return '后续同代际RR',event
    return '业务结束截尾',None


def rate(block,seconds):
    flags=int(block['窗口标志'])
    bit=2 if seconds==10 else 4
    if not flags&bit or flags&8:return None
    require(int(block['快照纳秒'])-int(block['代际起点纳秒'])>=seconds*10**9,'完整窗口与代际年龄矛盾')
    delta=int(block['十秒真实增量' if seconds==10 else '三十秒真实增量'])
    require(0<=delta<=int(block['当前RC']),'同块读取增量不合法')
    return delta/seconds


def verify_source(stage,root):
    expected=json.loads((stage/'独立核验.json').read_text())
    require(expected['验收通过'] and expected['策略']=='space','来源不是通过验收的Greedy组')
    require(not json.loads((stage/'阶段归档.json').read_text())['客机仍存活'],'来源客机尚未退出')
    prefix='/home/femu/'+root.parent.name+'/'+root.name+'/'
    manifest={name:sha for sha,name in (s.split(maxsplit=1) for s in (root/'logs.sha256').read_text().splitlines())}
    # wrapper.exit由EXIT trap在清单生成后写入，只检查退出码，不伪称其在原清单中。
    names=('run.zenfs.log','run.log','run.exit','verify.exit','strict.business.json',
        'software.before.log','software.after.log','zones.initial.json')
    checked={}
    for name in names:
        require(prefix+name in manifest,'来源清单缺少：'+name)
        sha=hashlib.sha256((root/name).read_bytes()).hexdigest()
        require(sha==manifest[prefix+name],'来源原始哈希不符：'+name);checked[name]=sha
    require(all((root/n).read_text().strip()=='0' for n in ('run.exit','wrapper.exit','verify.exit')),'来源未正常完成')
    strict=json.loads((root/'strict.business.json').read_text())
    require(strict['启用']==1 and strict['历史最高RC']<=1024,'来源严格RR配置或最高RC不符')
    return expected,checked


def parse(lines):
    frames=[];frame=None;rr=[];events=defaultdict(list);retired={};last_risk={};active_rr=None
    def new_frame(i,line,row):
        return dict(触发行=i,触发时刻=clock(line),空闲比例=int(row['空闲比例'].rstrip('%')),
            候选={},文件=defaultdict(dict),代际={},成本={},比例资格={},基础资格={},目标资格={},轮次=None,决策=None)
    for i,line in enumerate(lines,1):
        if 'GC 检查：' in line:
            row=fields(line,'GC 检查：')
            require(row['启动水平']=='20%','来源改变了GC20%启动水平')
            if row['进入选区']=='1':frame=new_frame(i,line,row);frames.append(frame)
        elif 'RR风险状态：' in line:
            row=fields(line,'RR风险状态：');last_risk[int(row['Zone'])]=row
        elif 'V1后续文件退役：' in line:
            match=re.search(r'V1后续文件退役：文件编号=(\d+)，文件名=[^，]+，最后链接已删除，元数据持久化成功',line)
            require(match is not None,'文件退役日志不完整')
            retired[int(match[1])]=(i,clock(line))
        elif 'Zone 维护观测：阶段=ZC候选，' in line and frame is not None:
            row=fields(line,'Zone 维护观测：');frame['代际'][int(row['Zone'])]=row
        elif '候选范围资格：' in line and frame is not None:
            row=fields(line,'候选范围资格：');frame['比例资格'][int(row['Zone'])]=row
        elif '简单双因素候选：' in line and frame is not None:
            row=fields(line,'简单双因素候选：');frame['基础资格'][int(row['Zone'])]=row
        elif '预留目标资格候选：' in line and frame is not None:
            row=fields(line,'预留目标资格候选：');frame['目标资格'][int(row['Zone'])]=row
        elif 'V1影子候选：' in line and frame is not None:
            row=fields(line,'V1影子候选：')
            require('设备代际' in row,'缺少候选块身份和窗口')
            number=int(row['轮次']);require(frame['轮次'] in (None,number),'同GC检查混入其他轮次')
            frame['轮次']=number;frame['候选'][int(row['Zone'])]=(row,i)
        elif 'V1影子文件：' in line and frame is not None:
            row=fields(line,'V1影子文件：')
            files=frame['文件'][int(row['Zone'])];fid=int(row['文件编号'])
            require(fid not in files,'同帧文件与Zone重复')
            files[fid]=int(row['区内有效字节'])
        elif '统一成本：' in line and frame is not None:
            row=fields(line,'统一成本：');frame['成本'][int(row['Zone'])]=row
        elif '统一决策：' in line:
            require(frame is not None,'决策前没有GC检查')
            row=fields(line,'统一决策：');require(frame['轮次']==int(row['轮次']),'决策轮次不一致')
            frame['决策']=row;frame['决策行']=i;frame['决策时刻']=clock(line)
            if row['实际存在']=='1':
                zone=int(row['实际Zone']);diag=frame['代际'][zone]
                generation=int(diag['主机代际'].split('/')[-1])
                events[zone].append(dict(类型='ZC',行=i,时刻=clock(line),Zone=zone,主机代际=generation))
        elif 'RR 开始：' in line:
            match=re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节',line)
            require(match is not None and active_rr is None,'RR开始不完整或发生嵌套')
            zone,blocks,valid=map(int,match.groups());risk=last_risk.get(zone)
            require(risk is not None and risk['可信']=='1' and int(risk['最大RC'])==1024 and int(risk['阈值'])==1024,
                'RR源缺少可信的真实阈值和代际')
            active_rr=dict(类型='RR',编号=len(rr)+1,行=i,时刻=clock(line),Zone=zone,
                主机代际=int(risk['代际']),有效字节=valid,达阈值块数=blocks,复制=defaultdict(int),目标=[],完成=False)
            rr.append(active_rr);events[zone].append(active_rr)
        elif 'V1后续迁移复制：' in line and active_rr is not None:
            row=fields(line,'V1后续迁移复制：')
            require(row['复制成功']=='1' and int(row['源Zone'])==active_rr['Zone'],'RR复制源或结果不符')
            active_rr['复制'][int(row['文件编号'])]+=int(row['有效字节'])
            target=int(row['目标Zone'])//ZONE_BYTES
            if target not in active_rr['目标']:active_rr['目标'].append(target)
        elif 'RR 完成：' in line:
            match=re.search(r'RR 完成：Zone (\d+) 已重置',line)
            require(active_rr is not None and match is not None and int(match[1])==active_rr['Zone'],'RR完成没有对应开始')
            active_rr.update(完成=True,完成行=i);active_rr=None
        elif 'Zone 重置诊断：' in line:
            row=fields(line,'Zone 重置诊断：')
            if row['状态']=='成功':
                before,after=map(int,row['主机代际'].split('/'));require(after==before+1,'Reset代际不递增')
                zone=int(row['Zone']);events[zone].append(dict(类型='Reset',行=i,时刻=clock(line),Zone=zone,
                    主机代际=before,新主机代际=after))
    require(active_rr is None and all(r['完成'] for r in rr),'业务结束仍有RR未完成')
    return frames,rr,events,retired


def analyze(stage):
    stage=Path(stage);matches=list(stage.glob('*/full-hot-space/run.zenfs.log'))
    require(len(matches)==1,'无法唯一定位来源Greedy业务日志');root=matches[0].parent
    expected,hashes=verify_source(stage,root)
    lines=(root/'run.zenfs.log').read_text().splitlines()
    plans=model_decisions(lines)
    for plan in plans:
        safe=[r for r in plan['成本'] if r['安全']=='1']
        require(all(int(r['RR抵扣'])==0 and int(r['有效'])+int(r['回收'])==ZONE_BYTES for r in safe),'来源混入RR收益或容量改变')
        best=min(safe,key=lambda r:(int(r['有效']),int(r['Zone']))) if safe else None
        require(int(plan['决策']['实际Zone'])==(int(best['Zone']) if best else 0),'来源不是最少有效量Greedy')
    frames,rr,events,retired=parse(lines)
    require(len(plans)==len(frames)==expected['ZC次数'] and len(rr)==expected['RR次数'],
        'GC检查、决策、RR与既有验收次数不一致')
    records=[];end=clock(lines[-1]);rr_by_id={r['编号']:r for r in rr}
    for frame in frames:
        require(frame['决策'] is not None,'GC检查没有完整决策')
        selected=int(frame['决策']['实际Zone']);greedy_valid=int(frame['成本'][selected]['有效'])
        for zone,(block,line_number) in frame['候选'].items():
            diag=frame['代际'][zone];gens=list(map(int,diag['主机代际'].split('/')))
            generation_known=len(set(gens))==1 and diag['重置窗稳定']=='1'
            cost=frame['成本'].get(zone);files=frame['文件'][zone]
            valid=int(block['有效字节']);coverage=sum(files.values())==valid
            safe=bool(cost and cost['安全']=='1')
            require(not safe or (generation_known and coverage),'实际安全候选缺少稳定代际或完整文件覆盖')
            basic=frame['基础资格'].get(zone,{});target=frame['目标资格'].get(zone,{})
            excluded=[]
            if not generation_known:excluded.append('主机代际不稳定')
            if not coverage or diag['覆盖完整']!='1':excluded.append('文件快照覆盖不完整')
            if diag['含非SST']=='1':excluded.append('含非SST')
            if basic.get('空目标资格')=='0':excluded.append('当前目标容量准入未通过')
            if not safe and not excluded:excluded.append('其他准入或复核未通过')
            row=dict(轮次=frame['轮次'],Zone编号=zone//ZONE_BYTES,Zone字节地址=zone,
                决策行=frame['决策行'],候选行=line_number,决策时刻=frame['决策时刻'],主机代际=gens[-1],
                设备代际=int(block['设备代际']),设备会话=block['设备会话'],块编号=int(block['块编号']),PPA=block['PPA'],
                有效字节=valid,回收余量字节=ZONE_BYTES-valid,最大RC=int(block['当前RC']),
                十秒真实增量=int(block['十秒真实增量']),三十秒真实增量=int(block['三十秒真实增量']),
                十秒读取每秒=rate(block,10),三十秒读取每秒=rate(block,30),窗口标志=int(block['窗口标志']),
                统计准入=int(block['统计准入']),共同安全资格=safe,主机代际可信=generation_known,
                文件覆盖字节=sum(files.values()),文件覆盖完整=coverage,
                未通过当前准入原因='；'.join(excluded) if not safe else '',
                当前目标资格=basic.get('空目标资格'),目标物理上界字节=int(target['物理上界字节']) if target else None,
                预留剩余字节=int(target['预留剩余字节']) if target else None,
                进入成本排序=cost is not None,当轮选中=zone==selected,GreedyZone编号=selected//ZONE_BYTES,
                Greedy有效字节=greedy_valid,相对Greedy额外有效字节=valid-greedy_valid,
                原比例资格=int(frame['比例资格'][zone]['原比例资格']),
                当前扩大比例资格=int(frame['比例资格'][zone]['实际比例资格']),
                文件编号=sorted(files),文件初始字节=files)
            if not generation_known:outcome,event='代际无法衔接',None
            elif row['当轮选中']:outcome,event='当轮ZC选中',None
            else:outcome,event=follow(row,events[zone])
            row.update(后续结局=outcome,后续事件行=event['行'] if event else None,
                到后续事件秒=event['时刻']-row['决策时刻'] if event else None,
                剩余观察秒=end-row['决策时刻'],后续RR编号=event['编号'] if event and event['类型']=='RR' else None,
                后续RR有效字节=event['有效字节'] if event and event['类型']=='RR' else None)
            if event and event['类型']=='RR':
                require(event['完成'] and sum(event['复制'].values())==event['有效字节'],'后续RR实际复制未闭合')
                survived=sum(n for fid,n in event['复制'].items() if fid in files)
                cohort_known=coverage and survived==event['有效字节'] and all(n<=files.get(fid,0) for fid,n in event['复制'].items())
                require(not safe or cohort_known,'安全满区候选的后续RR数据不是原文件集合子集')
                row['原文件实际存活至RR字节']=survived
                row['RR文件子集核验通过']=cohort_known
            later=[retired[fid] for fid in files if fid in retired and retired[fid][0]>row['决策行']]
            row['原文件业务结束前全部退役']=bool(files) and len(later)==len(files)
            row['原文件全部退役等待秒']=max(t for _,t in later)-row['决策时刻'] if row['原文件业务结束前全部退役'] else None
            records.append(row)
    safe=[r for r in records if r['共同安全资格'] and not r['当轮选中']]
    opportunities=[r for r in safe if r['后续结局']=='后续同代际RR']
    rr_groups=defaultdict(list)
    for r in opportunities:rr_groups[r['后续RR编号']].append(r)
    unique=[]
    for identifier,group in sorted(rr_groups.items()):
        earliest=min(group,key=lambda r:r['决策行']);latest=max(group,key=lambda r:r['决策行'])
        event=rr_by_id[identifier]
        unique.append(dict(RR编号=identifier,Zone编号=event['Zone']//ZONE_BYTES,主机代际=event['主机代际'],
            RR开始行=event['行'],RR有效字节=event['有效字节'],此前可选GC轮次=[r['轮次'] for r in group],
            最早候选=earliest,最近候选=latest))
    profiles=[]
    for event in rr:
        observations=[r for r in records if r['后续RR编号']==event['编号']]
        actionable=[r for r in observations if r['共同安全资格']]
        if event['行']<frames[0]['触发行']:kind='首次GC前已发生RR'
        elif actionable:kind='有通过当前共同准入的潜在机会'
        elif observations:kind='有满区观察但未通过当前共同准入'
        else:kind='未见同代际满区候选前史'
        latest=max(observations,key=lambda r:r['决策行']) if observations else None
        profiles.append(dict(RR编号=event['编号'],Zone编号=event['Zone']//ZONE_BYTES,分类=kind,
            最近观察=latest,安全观察数=len(actionable),RR实际目标Zone编号=event['目标']))
    windows={str(w):dict(候选观察次数=sum(r['到后续事件秒']<=w for r in opportunities),
        不同RR事件数=len({r['后续RR编号'] for r in opportunities if r['到后续事件秒']<=w}),
        涉及GC轮数=len({r['轮次'] for r in opportunities if r['到后续事件秒']<=w})) for w in (10,30,60)}
    return dict(来源业务目录=str(root),来源核心SHA256=hashes,新启动客机=False,选区修改=False,
        GC触发轮数=len(frames),实际Greedy决策数=len(plans),业务RR次数=len(rr),
        所有满区候选观察数=len(records),共同安全候选观察数=sum(r['共同安全资格'] for r in records),
        安全未选候选观察数=len(safe),安全未选结局=dict(Counter(r['后续结局'] for r in safe)),
        潜在同代际RR观察次数=len(opportunities),去重潜在RR事件数=len(unique),
        有潜在机会的GC轮数=len({r['轮次'] for r in opportunities}),近期窗口秒=windows,
        全部RR分布=dict(Counter(r['分类'] for r in profiles)),全部RR前史=profiles,
        潜在机会中通过原比例资格的RR数=len({r['后续RR编号'] for r in opportunities if r['原比例资格']}),
        各候选=records,去重RR事件=unique,
        口径='原Greedy真实运行的只读回溯；扩大候选及共同安全检查不是stock ZenFS；事件按Zone与主机Reset代际关联，不跨Reset；同一后续RR不重复累计为多次收益；不是可避免RR的因果估计',
        未确认='后续真正触发的物理块编号未逐次记录；预测时只有最大RC块自己的真实窗口。RR时设备C2代际未直接采样，使用同一进程的连续主机Reset记录截止追踪。')


def write(result,output):
    output=Path(output);require(not output.exists(),'不覆盖派生分析');output.mkdir(parents=True)
    (output/'机会核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    rows=result['各候选'];columns=[k for k in rows[0] if k not in ('文件编号','文件初始字节','决策时刻')]
    # 可选字段在部分行才出现，统一列出，空值保留为空而非伪造零。
    columns+=sorted({k for r in rows for k in r if k not in columns and k not in ('文件编号','文件初始字节','决策时刻')})
    with (output/'逐轮候选与后续.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    summary={k:v for k,v in result.items() if k not in ('各候选','去重RR事件','全部RR前史')}
    (output/'汇总.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    windows='\n'.join(f"| {w}秒 | {v['候选观察次数']} | {v['不同RR事件数']} | {v['涉及GC轮数']} |" for w,v in result['近期窗口秒'].items())
    examples=[]
    for event in result['去重RR事件']:
        r=event['最近候选'];observed_rate='未知（窗口未满或不可用）' if r['十秒读取每秒'] is None else str(r['十秒读取每秒'])
        examples.append(f"| {event['RR编号']} | {event['Zone编号']} | {r['轮次']} | {r['最大RC']} | {observed_rate} | {r['到后续事件秒']:.3f} | {r['相对Greedy额外有效字节']/(1<<20):.3f} | {event['RR有效字节']/(1<<20):.3f} |")
    stopped=[r for r in rows if r['共同安全资格'] and not r['当轮选中'] and
        r['后续结局']=='业务结束截尾' and r['剩余观察秒']>=30 and r['最大RC']>=922 and r['十秒读取每秒']==0]
    stopped_text='本样本未找到满足筛选条件的高RC停读示例。'
    if stopped:
        r=stopped[0];stopped_text=f"第{r['轮次']}轮Zone{r['Zone编号']}最大RC={r['最大RC']}，同块十秒增量为0，随后剩余{r['剩余观察秒']:.3f}秒业务观察未见RR或Reset。说明累计RC高本身不等于近期必然RR；不能把截尾结果外推为永不RR。"
    cost_examples=[]
    for event in result['去重RR事件'][:3]:
        r=event['最近候选'];extra=r['相对Greedy额外有效字节'];future=event['RR有效字节']
        comparison=f"仅在这两笔复制间比较，提前改选就多出{(extra-future)/(1<<20):.3f}MiB。" if extra>future else \
            f"两笔账之间有{(future-extra)/(1<<20):.3f}MiB余量，但后续成本尚未计入。"
        cost_examples.append(f"第{r['轮次']}轮Zone{r['Zone编号']}当时有效量{r['有效字节']/(1<<20):.3f}MiB，Greedy为{r['Greedy有效字节']/(1<<20):.3f}MiB；改选多搬{extra/(1<<20):.3f}MiB，后续那次RR实际搬{future/(1<<20):.3f}MiB。{comparison}")
    blocked=[p['最近观察'] for p in result['全部RR前史'] if p['分类']=='有满区观察但未通过当前共同准入']
    target_blocked=[r for r in blocked if r['当前目标资格']=='0']
    incomplete=[r for r in blocked if not r['文件覆盖完整']]
    excess=[r['目标物理上界字节']-ZONE_BYTES for r in target_blocked if r['文件覆盖完整'] and
        r['目标物理上界字节'] is not None and r['目标物理上界字节']>ZONE_BYTES]
    target_text=f"未通过当前准入的RR中，最近一次满区观察有{len(target_blocked)}次目标资格未通过、{len(incomplete)}次文件快照覆盖不完整，两类可能重叠。"
    if excess:
        target_text+=f"其中{len(excess)}次完整覆盖对象的迁移保守上界超过单Zone容量，超过字节数为{sorted(set(excess))}。"
    target_text+='当前目标资格是软件准入，不等于硬件无法搬迁；后续RR成功与当时GC准入可能采用不同的目标管理路径，需要单独判断。'
    multiple=sum(len(p['RR实际目标Zone编号'])>1 for p in result['全部RR前史'] if
        p['分类']=='有满区观察但未通过当前共同准入')
    target_text+=f"这类后续RR中有{multiple}次实际使用了多个目标Zone，已从逐文件复制记录确认。"
    text=f'''# 实验4：Greedy未选候选的后续RR机会回溯

本次没有新启动客机或更改victim selection。使用此前已验收真实Greedy运行的业务阶段，重新验证原始日志哈希、完成状态、严格RR及每轮实际Greedy选择；没有把核验阶段的顺序读取混入业务后续RR。

来源：{result['来源业务目录']}。

共{result['GC触发轮数']}轮GC，业务RR{result['业务RR次数']}次。记录{result['所有满区候选观察数']}次满区候选观察，其中{result['共同安全候选观察数']}次通过共同安全资格；剔除当轮选中项后，有{result['安全未选候选观察数']}次安全未选观察。其后续结局为：{result['安全未选结局']}。

安全未选候选中，{result['潜在同代际RR观察次数']}次观察后来在同一主机Reset代际独立RR，去重对应{result['去重潜在RR事件数']}次RR，涉及{result['有潜在机会的GC轮数']}个GC轮次。重复看到同一Zone同一代际，不重复记成多个独立收益。

全部RR的前史分布为：{result['全部RR分布']}。这里的“安全”是通过当前软件共同准入，不是对硬件可迁移性的绝对判断。被当前目标容量准入排除，不能解释成硬件必然搬不动；保守上界、预留池组织和逐片段迁移能力需要单独区分。

| GC决策至RR窗口 | 候选观察数 | 去重后续RR数 | 涉及GC轮数 |
|---|---:|---:|---:|
{windows}

候选已经是Full，追踪期间遇到首次后续ZC或Reset就截止，不跨Zone复用匹配。后续同代际RR的实际复制文件及字节，均核对为原候选文件集合的剩余子集，证明有原有效数据存活到RR，而不只是物理地址碰巧相同。

下表按每个去重RR最近一次安全候选观察展示。额外有效字节是当时改选相对Greedy需要多复制的量，不是实际节省；RR实际有效量也不能直接全额抵扣。

| RR编号 | Zone | 最近GC轮次 | 当时最大RC | 同块10秒读每秒 | 随后RR等待秒 | 额外当前复制MiB | RR时有效MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(examples)}

候选读取增量来自C2同一物理块的真实窗口，不计算两次Zone最大RC之差。10/30秒完整窗口标志未就绪或历史丢失时，速率保留未知；不以零代替未知。事件等待时间使用同份客机日志时钟，不与设备C2纳秒时钟混减。

这统计的是潜在联合维护机会，不是已证明可避免的RR数量。每轮只能选一个victim；改选可能多复制有效数据、少回收空间，也可能在目标位置再次RR。当前Greedy使用扩大候选与共同安全检查，不冒称未经修改的stock ZenFS。原比例资格也保留在CSV，可单独筛查。

这组潜在机会中，通过原动态无效比例门槛的不同RR数为{result['潜在机会中通过原比例资格的RR数']}。因此不应把本结果直接套到原候选范围，或把扩大候选后的观察声称为stock ZenFS已经允许的选择。

{stopped_text}

还需要比较额外成本。空间回收减少及目标后续RR尚未计入，不能因候选后来真实RR就声称值得改选。

{chr(10).join(cost_examples)}

{target_text}

业务结束未见RR按截尾处理，不判断永不RR。后续真正触发块的物理编号和RR瞬间的设备C2代际并未直接记录，这些边界不能靠推测补齐。这里只利用连续主机Reset日志保证不跨复用，并报告候选时最大块的已测身份与速率。

完整结果：机会核验.json；便于查看的候选表：逐轮候选与后续.csv。来源原始日志保持不动。本轮仅做只读统计，没有编译或运行新机制。
'''
    (output/'实验4分析.md').write_text(text)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=analyze(args.source);write(result,args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('各候选','去重RR事件','全部RR前史')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
