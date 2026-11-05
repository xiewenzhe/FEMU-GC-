"""重建真实W/H共区与首次RR；按Reset代际追踪，不把入池等同共区。"""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import re
from statistics import median
import tempfile
from analyze_all_blocks_dbbench import fields
from analyze_flush_control import context
from analyze_flush_retirement import resolve_task
from analyze_reproducibility import stamp
from analyze_sst_compaction import events
from audit_colocation_transactions import audit
from ycsb_af import digest,require

ZONE=128<<20


class Replay:
    """只回放证据事件；持有位置到所有同源片段提交完成，避免提前移除。"""
    def __init__(self,hot,warm,initial):
        self.hot=set(hot);self.warm=set(warm);self.where={}
        self.members=defaultdict(set);self.epochs=Counter();self.dead=set()
        self.born={};self.episodes={};self.remaining=Counter()
        for name,slots in initial.items():
            self.where[name]=set(slots)
            for slot in slots:self.members[slot].add(name)

    def register(self,slot,line,reason):
        if not self.members[slot]&self.hot:return
        for name in sorted(self.members[slot]&self.warm):
            self.episodes.setdefault((name,slot),dict(文件=name,Zone=slot[0],代际=slot[1],
                首共区行=line,首次原因=reason,首次RR=None,首次RR行=None,结局=None))

    def birth(self,name,slots,line):
        require(name in self.warm and name not in self.born, 'W身份错误或重复入队')
        require(all(self.epochs[z]==e for z,e in slots), 'W写入跨过Reset')
        self.born[name]=line;self.where[name]=set(slots)
        for slot in slots:self.members[slot].add(name);self.register(slot,line,'新W进入已有H区')

    def retire(self,name):
        require(name in self.where and name not in self.dead,'重复退役或缺少出生')
        self.dead.add(name)
        for slot in self.where.pop(name):self.members[slot].remove(name)

    def rr(self,zone,ordinal,line,planned):
        slot=(zone,self.epochs[zone])
        actual={q['文件名'] for q in planned if q['文件名'] in self.warm}
        require(self.members[slot]&self.warm==actual, 'RR源的W集合与实际复制不符')
        for record in self.episodes.values():
            if (record['Zone'],record['代际'])==slot and record['首次RR'] is None:
                record.update(首次RR=ordinal,首次RR行=line,
                    结局='RR前逻辑退役' if record['文件'] in self.dead else 'RR到来仍存活')
        for q in planned:
            if q['文件名'] in self.hot|self.warm:
                self.remaining[(q['文件名'],zone,self.epochs[zone])]+=1

    def move(self,q,line):
        name=q['文件名']
        if name not in self.hot|self.warm:return
        src=(int(q['源Zone']),self.epochs[int(q['源Zone'])])
        dst=(int(q['目标Zone']),self.epochs[int(q['目标Zone'])])
        key=(name,*src)
        require(src!=dst and src in self.where[name] and self.remaining[key]>0,'迁移源、代际或片段数不符')
        self.remaining[key]-=1
        if not self.remaining[key]:
            self.where[name].remove(src);self.members[src].remove(name)
        self.where[name].add(dst);self.members[dst].add(name)
        self.register(dst,line,'H迁入已有W区' if name in self.hot else 'W必要迁移至H区')

    def reset(self,zone):
        slot=(zone,self.epochs[zone])
        require(not self.members[slot], 'Reset时仍有已追踪的有效H/W')
        for record in self.episodes.values():
            if (record['Zone'],record['代际'])==slot and record['首次RR'] is None:
                record['结局']='先Reset，未观察到同代际RR'
        self.epochs[zone]+=1

    def first(self,name):
        rows=[r for r in self.episodes.values() if r['文件']==name]
        if not rows:return []
        first=min(r['首共区行'] for r in rows)
        return [r for r in rows if r['首共区行']==first]


def prepare(root,proof,tags):
    lines=(root/'run.zenfs.log').read_text().splitlines()
    with tempfile.TemporaryDirectory(prefix='rrzc-cohorts-') as tmp:
        p=Path(tmp);(p/'zenfs.log').write_text('受控放置业务开始：派生窗口\n'+'\n'.join(lines)+'\n受控放置业务截止：派生窗口\n')
        checked=audit(p)
    require(checked['事务']==proof['事务'] and checked['维护账目']==proof['维护'], '原维护账目重算不同')
    tasks=[resolve_task(lines,t) for t in proof['事务']]
    require(len(tasks)==7 and all(t['类型']=='RR' for t in tasks), '本分析只适用于已验收的开启组七次RR、零ZC')
    records=events(root/'run.rocksdb.log')
    jobs={r['记录']['job'] for r in records if r['记录'].get('event')=='flush_started'}
    created={'/rrzc-dbbench/'+str(r['记录']['file_number']).zfill(6)+'.sst':r for r in records
             if r['记录'].get('event')=='table_file_creation' and r['记录']['job'] in jobs and r['记录']['status']=='OK'}
    require(len(created)==360, '真实Flush文件范围不同')
    pending={};alloc=defaultdict(list);epochs=Counter();anchors={};fallback=[]
    for n,line in enumerate(lines,1):
        tid=line.split()[1]
        if '协同池取得目标：' in line:
            f=fields(line,'协同池取得目标：')
            if f['阶段']=='新文件' and f['类别']=='2':
                require(tid not in pending,'同线程分配尚未关联元数据持久化')
                z=int(f['Zone']);pending[tid]=dict(Zone=z,代际=epochs[z],行=n,原因=f['原因'])
        if 'Syncing metadata for: ' in line and tid in pending:
            name=line.split('Syncing metadata for: ',1)[1].strip()
            require(name in created,'W分配关联到了其他文件')
            alloc[name].append(pending.pop(tid))
        if 'New random access file: ' in line:
            name=line.split('New random access file: ',1)[1].split(' direct:',1)[0]
            if name in created:anchors.setdefault(name,n)
        if '协同池回退：' in line:
            f=fields(line,'协同池回退：')
            matches=[t for t in tasks if t['原始开始行']<n<t['原始结束行'] and t['原始选择行'].split()[1]==tid]
            require(f['类别']=='2' and len(matches)==1, '出现新文件回退或未知回退，不能套用本轮全入池分类')
            fallback.append(dict(行=n,阶段='RR必要迁移',原因=f['原因']))
        if 'Zone 重置诊断：' in line:
            f=fields(line,'Zone 重置诊断：');require(f['状态']=='成功','失败Reset')
            epochs[int(f['Zone'])]+=1
    require(not pending and set(alloc)==set(anchors)==set(created), '新W分配或读打开证据不完整')
    witness=set()
    for path in root.glob('布局-*.extent.csv'):
        with path.open() as stream:witness.update((r['文件'],int(r['Zone'])) for r in csv.DictReader(stream))
    witness.update((c['字段']['文件名'],int(c['字段']['源Zone'])) for t in tasks for c in t['复制'])
    copied=Counter(c['字段']['文件名'] for t in tasks for c in t['复制'] if c['字段']['文件名'] in created)
    require(max(copied.values())==1,'W出现多次迁移，须重新核验原始分配的正片段证据')
    sizes={c['字段']['文件名']:int(c['字段']['有效字节']) for t in tasks for c in t['复制'] if c['字段']['文件名'] in created}
    for name,rows in alloc.items():
        require(1<=len(rows)<=2 and len({(r['Zone'],r['代际']) for r in rows})==len(rows), '原始分配多目标或代际重复')
        require(all((name,r['Zone']) in witness for r in rows),'分配目标缺少实际正extent证据')
        if len(rows)==2 and name in copied:
            require(sizes[name]<created[name]['记录']['file_size'],'可能是无数据预分配，不能冒称第二目标已存数据')
    # 核心回放只用ZenFS原始行顺序。跨日志时间只验证出生锚点附近没有敏感状态变化。
    sensitive=[]
    for t in tasks:
        sensitive.append((stamp(t['原始选择行']),{t['Zone']}))
        for c in t['复制']:
            q=c['字段']
            if q['文件名'] in tags:
                commit=next(a for a in t['提交'] if a['字段']['文件编号']==q['文件编号'] and a['原始行']>c['原始行'])
                sensitive.append((commit['时刻'],{int(q['源Zone']),int(q['目标Zone'])}))
    for line in lines:
        if 'Zone 重置诊断：' in line:sensitive.append((stamp(line),{int(fields(line,'Zone 重置诊断：')['Zone'])}))
    for name,rows in alloc.items():
        a=min(stamp(lines[r['行']-1]) for r in rows);b=created[name]['记录']['time_micros']*1000
        anchor=stamp(lines[anchors[name]-1]);require(a<=anchor<=b,'写入/读打开/创建记录顺序不同')
        zones={r['Zone'] for r in rows}
        require(not any(a<=time<=b and zones&changed for time,changed in sensitive),
                '文件创建边界内H/RR/Reset变化，不能用单一出生锚点分类')
    return lines,tasks,records,created,alloc,anchors,fallback


def analyze_arm(root,proof,tags,arm):
    lines,tasks,records,created,alloc,anchors,fallback=prepare(root,proof,tags)
    initial=defaultdict(set)
    with (root/'布局-0.extent.csv').open() as stream:
        for r in csv.DictReader(stream):
            if r['文件'] in tags:initial[r['文件']].add((int(r['Zone']),0))
    replay=Replay(tags,created,initial);timeline=[]
    for name,line in anchors.items():timeline.append((line,'birth',name))
    for n,line in enumerate(lines,1):
        if 'V1后续文件退役：' in line:
            name=fields(line,'V1后续文件退役：')['文件名']
            if name in created:timeline.append((n,'retire',name))
        if 'Zone 重置诊断：' in line:timeline.append((n,'reset',int(fields(line,'Zone 重置诊断：')['Zone'])))
    for i,t in enumerate(tasks,1):
        timeline.append((t['原始开始行'],'rr',(i,t)))
        for c in t['复制']:
            q=c['字段'];commit=next(a for a in t['提交'] if a['字段']['文件编号']==q['文件编号'] and a['原始行']>c['原始行'])
            timeline.append((commit['原始行'],'move',q))
    require(len({n for n,_,_ in timeline})==len(timeline),'多个状态事件落于同一原始行')
    for n,kind,value in sorted(timeline,key=lambda t:t[0]):
        if kind=='birth':replay.birth(value,{(r['Zone'],r['代际']) for r in alloc[value]},n)
        elif kind=='retire':replay.retire(value)
        elif kind=='reset':replay.reset(value)
        elif kind=='move':replay.move(value,n)
        else:
            ordinal,t=value;replay.rr(t['Zone'],ordinal,n,[c['字段'] for c in t['复制']])
    require(not any(replay.remaining.values()),'维护片段未全部提交')
    final=defaultdict(set)
    with (root/'布局-61.extent.csv').open() as stream:
        for r in csv.DictReader(stream):
            if r['文件'] in set(tags)|set(created):final[r['文件']].add((int(r['Zone']),replay.epochs[int(r['Zone'])]))
    require({k:v for k,v in replay.where.items() if v}==dict(final),'末端真实extent与回放位置不一致')
    starts=[r for r in records if r['记录'].get('event')=='compaction_started']
    ends={r['记录']['job']:r for r in records if r['记录'].get('event')=='compaction_finished'}
    require(set(ends)=={r['记录']['job'] for r in starts},'Compaction未闭合')
    files=[];grouped=defaultdict(list)
    for name in sorted(created):
        first=replay.first(name);record=first[0] if len(first)==1 else None
        status=('池内未见共区' if not first else '多区首遇，单列' if len(first)>1 else
                record['结局'] or '观察窗内无后继RR')
        age=(stamp(lines[record['首共区行']-1])-stamp(lines[anchors[name]-1]))/1e9 if record else None
        in_progress=[]
        if record:
            now=stamp(lines[record['首共区行']-1]);number=int(Path(name).stem)
            in_progress=[r['记录']['job'] for r in starts
                if number in {v for k,values in r['记录'].items() if re.fullmatch('files_L[0-9]+',k) for v in values}
                and r['记录']['time_micros']*1000<=now<ends[r['记录']['job']]['记录']['time_micros']*1000]
        row=dict(臂=arm,文件=name,原始分配目标数=len(alloc[name]),出生读打开原始行=anchors[name],
            首次共区原始行=record['首共区行'] if record else None,
            首次共区ZoneID=record['Zone']//ZONE if record else None,
            首次共区日志Reset序数=record['代际'] if record else None,
            首次共区原因=record['首次原因'] if record else '无',结局=status,
            首次RR序号=record['首次RR'] if record else None,共区时文件年龄秒=age,
            共区时已在途Compaction输入=bool(in_progress),在途任务='/'.join(map(str,in_progress)),
            末端已逻辑退役=name in replay.dead,实际RR复制字节=sum(int(c['字段']['有效字节'])
                for t in tasks for c in t['复制'] if c['字段']['文件名']==name))
        files.append(row)
        if record:grouped[(row['首次共区原因'],status)].append(row)
    groups=[]
    for (reason,status),rows in grouped.items():
        ages=[r['共区时文件年龄秒'] for r in rows]
        groups.append(dict(臂=arm,原因=reason,结局=status,文件数=len(rows),年龄最小秒=min(ages),
            年龄中位秒=median(ages),年龄最大秒=max(ages),已在途Compaction输入数=sum(r['共区时已在途Compaction输入'] for r in rows)))
    episodes=[dict(臂=arm,**r) for r in replay.episodes.values()]
    counts=dict(Counter(r['结局'] for r in files))
    summary=dict(全部W数=len(files),分类=counts,共区文件数=sum(bool(replay.first(name)) for name in created),
        全部共区片段期数=len(episodes),新W协同目标分配次数=sum(map(len,alloc.values())),
        新W普通回退次数=0,必要W迁移回退次数=len(fallback),
        首共区途径=dict(Counter(r['首次共区原因'] for r in files if r['首次共区原始行'] is not None)),
        同代际RR源集合与实际复制一致=True,全部Reset无追踪有效文件=True,末端位置与真实extent一致=True,
        原日志SHA256={n:digest(root/n) for n in ('run.zenfs.log','run.rocksdb.log','布局-0.extent.csv','布局-61.extent.csv')})
    return summary,files,episodes,groups,alloc


def analyze(source,output):
    require(not output.exists(),'不覆盖旧派生结果')
    state,_,tags=context(source);require(state['业务Flush来源W'],'不是开启组')
    summaries={};files=[];episodes=[];groups=[];allocation=[]
    for arm in ('p1','p2'):
        proof=json.loads((source/(arm+'-核验.json')).read_text())
        summary,a,b,c,d=analyze_arm(source/'逐臂采证'/arm,proof,tags,arm)
        summaries[arm]=summary;files+=a;episodes+=b;groups+=c
        allocation.extend(dict(臂=arm,文件=name,**r) for name,rows in d.items() for r in rows)
    keys=('文件','首次共区ZoneID','首次共区日志Reset序数','首次共区原因','结局','首次RR序号','实际RR复制字节')
    require([[r[k] for k in keys] for r in files if r['臂']=='p1']==
            [[r[k] for k in keys] for r in files if r['臂']=='p2'],'两轮首次共区分类不同')
    result=dict(来源=str(source),完整归档SHA256=state['镜像处理']['archive_sha256'],各臂=summaries,
        逐文件分类重复相同=True,分析源码SHA256=digest(Path(__file__)),
        边界='共区为SST初次读打开锚点之后的已追踪有效位置；未来退役只作回溯。没有后继RR为删失，不等于成功配对。')
    output.mkdir(parents=True)
    (output/'首次共区核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for name,rows in (('全部W首次共区.csv',files),('全部共区片段期.csv',episodes),('首遇特征与结局.csv',groups),('新W实际分配.csv',allocation)):
        with (output/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (output/'分析脚本.py').write_bytes(Path(__file__).read_bytes())
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();print(json.dumps(analyze(a.source,a.output)['各臂'],ensure_ascii=False,indent=2))
