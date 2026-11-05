"""只读审计真实维护片段链和同代际候选；重复复制不等于可避免复制。"""
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
import hashlib,json,re
from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import accounting

ZONE=128<<20


def timestamp(line):
    return datetime.strptime(line.split()[0],'%Y/%m/%d-%H:%M:%S.%f')


def parse(lines):
    epochs=Counter();active={};pending=defaultdict(list);live={}
    tasks=[];copies=[];candidates=[];zone_events=defaultdict(list);pair_bytes=Counter()
    retirements={};retirement_times={};finish={};last_cost={}
    for number,line in enumerate(lines,1):
        tid=line.split()[1];moment=timestamp(line)
        if '公共普通分配Finish代价：' in line:
            r=fields(line,'公共普通分配Finish代价：')
            if r['成功']=='1':
                z=int(r['Zone']);finish[z,epochs[z]]=dict(行=number,尾部=int(r['牺牲尾部字节']))
        if '时序成本：' in line:
            r=fields(line,'时序成本：');z=int(r['Zone']);n=int(r['轮次'])
            c=dict(轮次=n,Zone=z//ZONE,代际=epochs[z],行=number,时刻=moment,
                有效=int(r['有效']),安全=r['安全']=='1',ETA纳秒=int(r['ETA']),原RR复制估计=int(r['RR复制']))
            candidates.append(c);last_cost[n,z]=c
        rr=re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节，封闭剩余容量 (\d+)',line)
        decision=fields(line,'时序方案决策：') if '时序方案决策：' in line else None
        if rr or (decision and decision['实际存在']=='1'):
            assert tid not in active,'同线程维护嵌套，当前审计无法安全分类'
            kind='RR' if rr else 'ZC';z=int(rr[1] if rr else decision['实际Zone'])
            valid=int(rr[3]) if rr else last_cost[int(decision['轮次']),z]['有效']
            task=dict(编号=len(tasks)+1,类型=kind,Zone=z//ZONE,代际=epochs[z],行=number,
                时刻=moment,有效=valid,封闭尾部=int(rr[4]) if rr else 0,复制=[],源Reset=False,
                最近普通Finish=finish.get((z,epochs[z])))
            if decision:task['轮次']=int(decision['轮次'])
            active[tid]=task;tasks.append(task);zone_events[z].append(dict(类型=kind,行=number,代际=epochs[z],任务=task))
        if 'V1后续迁移复制：' in line:
            r=fields(line,'V1后续迁移复制：');assert r['复制成功']=='1' and tid in active
            src,dst=int(r['源Zone']),int(r['目标Zone']);size=int(r['有效字节']);fid=int(r['文件编号'])
            assert src//ZONE==active[tid]['Zone'] and size>0
            assert fid not in retirements,'退役文件仍被成功迁移，不能安全关联'
            key=(fid,src,epochs[src],int(r['源片段']),size);old=live.get(key)
            c=dict(编号=len(copies)+1,任务=active[tid]['编号'],维护=active[tid]['类型'],文件=fid,
                文件名=r['文件名'],源Zone=src//ZONE,目标Zone=dst//ZONE,源代际=epochs[src],目标代际=epochs[dst],
                源片段=int(r['源片段']),目标片段=int(r['目标片段']),字节=size,行=number,时刻=moment,
                前次复制=old['编号'] if old else None,映射提交行=None)
            if old:
                assert old['映射提交行'] is not None and old['映射提交行']<number
                c['重复类型']=old['维护']+'→'+c['维护']
                c['距离前次复制秒']=(moment-old['时刻']).total_seconds()
                pair_bytes[c['重复类型']]+=size
            copies.append(c);active[tid]['复制'].append(c['编号']);pending[tid,fid].append(c)
            zone_events[dst].append(dict(类型='目标收到迁移',行=number,代际=epochs[dst]))
        if 'V1后续映射提交：' in line:
            r=fields(line,'V1后续映射提交：');assert r['成功']=='1'
            fid=int(r['文件编号']);group=pending.pop((tid,fid),[]);assert group,'映射提交缺少复制'
            for c in group:
                c['映射提交行']=number
                live.pop((fid,c['源Zone']*ZONE,c['源代际'],c['源片段'],c['字节']),None)
                key=(fid,c['目标Zone']*ZONE,c['目标代际'],c['目标片段'],c['字节'])
                assert key not in live,'重复登记同一物理片段'
                live[key]=c
        if 'V1后续文件退役：' in line:
            r=fields(line,'V1后续文件退役：');fid=int(r['文件编号']);retirements[fid]=number
            retirement_times[fid]=dict(行=number,时刻=moment)
            live={key:value for key,value in live.items() if key[0]!=fid}
        if 'Zone 重置诊断：' in line:
            r=fields(line,'Zone 重置诊断：');assert r['状态']=='成功'
            z=int(r['Zone']);before,after=map(int,r['主机代际'].split('/'));assert after==before+1
            zone_events[z].append(dict(类型='Reset',行=number,代际=epochs[z]))
            if tid in active and active[tid]['Zone']==z//ZONE:active[tid]['源Reset']=True
            epochs[z]+=1
        if 'ZC 统计：' in line or 'RR 统计：' in line:
            assert tid in active
            t=active.pop(tid);match=re.search(r'(?:本轮|本次)迁移 (\d+) 字节',line);assert match
            total=int(match[1]);assert sum(copies[i-1]['字节'] for i in t['复制'])==total
            assert t['源Reset'],'维护未观察到源Reset'
            t.update(结束行=number,结束时刻=moment,实际复制=total)
    assert not active and not pending and all(c['映射提交行'] for c in copies)
    first_gc=min((t['行'] for t in tasks if t['类型']=='ZC'),default=len(lines)+1)
    rr_rows=[]
    for t in tasks:
        if t['类型']!='RR':continue
        opportunities=[]
        for c in candidates:
            if not c['安全'] or c['Zone']!=t['Zone'] or c['代际']!=t['代际'] or c['行']>=t['行']:continue
            event=next((e for e in zone_events[t['Zone']*ZONE] if e['行']>c['行']),None)
            if event and event['类型']=='RR' and event['任务']['编号']==t['编号']:
                opportunities.append(dict(轮次=c['轮次'],候选行=c['行'],候选有效=c['有效'],
                    到RR秒=(t['时刻']-c['时刻']).total_seconds(),预测ETA纳秒=c['ETA纳秒']))
        rr_rows.append(dict(RR序号=len(rr_rows)+1,任务=t['编号'],Zone=t['Zone'],有效=t['有效'],封闭尾部=t['封闭尾部'],
            源已写区间非有效字节上界=ZONE-t['有效']-t['封闭尾部'],
            首次GC之前=t['行']<first_gc,可行动候选前史=opportunities))
    for c in copies:
        retired=retirement_times.get(c['文件'])
        c['随后文件退役']=retired
        c['复制后到退役秒']=(retired['时刻']-c['时刻']).total_seconds() if retired else None
    return dict(任务=tasks,复制=copies,片段重复字节分布=dict(pair_bytes),
        片段重复复制次数=sum(c['前次复制'] is not None for c in copies),
        片段重复复制字节=sum(pair_bytes.values()),RR前史=rr_rows)


def audit(root):
    root=Path(root);output=root.parents[1]
    archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
    log=root/'run.zenfs.log';result=parse(log.read_text().splitlines());ledger=accounting(root)
    assert len(result['复制'])==ledger['成功复制事件']
    assert sum(c['字节'] for c in result['复制'])==ledger['总迁移字节']
    assert len([t for t in result['任务'] if t['类型']=='ZC'])==ledger['ZC次数']
    assert len([t for t in result['任务'] if t['类型']=='RR'])==ledger['RR次数']
    result.update(原始日志=str(log),SHA256=hashlib.sha256(log.read_bytes()).hexdigest(),
        维护账目=ledger,边界='物理片段、长度、文件ID、Reset代际及映射提交衔接才计重复；只统计完整业务窗口。'
        '没有精确衔接的同文件复制不算已证明重复。潜在候选前史不证明可避免RR。'
        '本地代际是业务开始后Reset次数，不是设备原始代际。非有效字节上界含填充，不等于精确净可写收益。')
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    a=p.parse_args();print(json.dumps(audit(a.root),ensure_ascii=False,indent=2,default=str))
