"""完整业务的逐指标归因：复制来源、未分类间隔、最慢Get维护路径。"""
import argparse
from array import array
from collections import Counter,defaultdict
import json
from pathlib import Path
import re
import struct
from analyze_reproducibility import request_rows
from analyze_phase_time import merge,overlap
from analyze_flush_wait import parse as parse_flush
from ycsb_af import digest,require


def event_records(path):
    for line in path.open():
        if 'EVENT_LOG_v1 ' in line:yield json.loads(line.split('EVENT_LOG_v1 ',1)[1])


def explain(root,report):
    log=(root/'run.zenfs.log').read_text();lines=log.splitlines()
    events=list(event_records(root/'run.rocksdb.log'))
    jobs={e['job']:('Flush' if e['event']=='flush_started' else 'Compaction') for e in events
          if e['event'] in ('flush_started','compaction_started')}
    files={e['file_number']:jobs[e['job']] for e in events if e['event']=='table_file_creation'}
    hot=set(map(int,re.findall(r'在线读热变化：文件编号=(\d+)，H=1', (root/'run.log').read_text())))
    copies=defaultdict(lambda:Counter());hot_count=defaultdict(lambda:Counter())
    for tx in report['事务']:
        for copy in tx['复制']:
            row=copy['字段'];fid=int(row['文件编号']);name=Path(row['文件名']).name
            number=int(name.removesuffix('.sst'))
            label='在线H' if fid in hot else files.get(number,'初始其他SST')
            copies[label][tx['类型']+'字节']+=int(row['有效字节'])
            if fid in hot:
                hot_count[fid][tx['类型']+'次数']+=1
                hot_count[fid][tx['类型']+'字节']+=int(row['有效字节'])
    require(sum(sum(x.values()) for x in copies.values())==report['维护']['总迁移字节'],'来源分解不闭合')
    starts,ends=array('Q'),array('Q');worst=None;first=last=0
    for row in request_rows(root/'请求提交.bin'):
        if not starts:first=row[4]
        starts.append(row[4]);ends.append(row[5]);last=row[6]
        if row[1]==0 and (worst is None or row[5]-row[4]>worst[5]-worst[4]):worst=row
    raw=(root/'等待区间.bin').read_bytes();require(raw[:16]==b'RRZCTM01'+bytes(8),'区间损坏')
    phases=list(struct.iter_unpack('<6Q',raw[16:]))
    occupied=merge([*zip(starts,ends),*[(max(a,first),min(b,last)) for k,a,b,*_ in phases if k in (1,2,3,4) and b>first and a<last]])
    gaps=[];cursor=first
    for a,b in occupied:
        if cursor<a:gaps.append((cursor,a))
        cursor=max(cursor,b)
    if cursor<last:gaps.append((cursor,last))
    raw=(root/'读取成本.bin').read_bytes();require(raw[:16]==b'RRZCRD01'+bytes(8),'成本损坏')
    work=defaultdict(list);at_percentile={}
    lat=next(r for r in report['API延迟'] if r['操作']=='READ')
    targets={'P99':lat['P99纳秒'],'P99.9':lat['P999纳秒'],'P99.99':lat['P9999纳秒']}
    tail={'1ms以上':dict(次数=0,API纳秒=0,锁等待纳秒=0,读调用纳秒=0),
          '20至60ms':dict(次数=0,API纳秒=0,锁等待纳秒=0,读调用纳秒=0)}
    for r in struct.iter_unpack('<10Q',raw[16:]):
        if r[0]==3:work[r[1]].append((max(first,r[4]),min(last,r[5])))
        if r[0]==1:
            ns=r[5]-r[4]
            for key,target in targets.items():
                distance=abs(ns-target)
                if key not in at_percentile or distance<at_percentile[key]['距离目标纳秒']:
                    at_percentile[key]=dict(距离目标纳秒=distance,序号=r[1],Key=r[2],API微秒=ns/1000,
                        锁等待微秒=r[6]/1000,读调用微秒=r[7]/1000,
                        命令微秒=r[8]/1000,其他微秒=(ns-r[6]-r[7])/1000)
            for name,match in (('1ms以上',ns>1000000),('20至60ms',20000000<=ns<=60000000)):
                if match:
                    t=tail[name];t['次数']+=1;t['API纳秒']+=ns;t['锁等待纳秒']+=r[6];t['读调用纳秒']+=r[7]
    explained={k:overlap(v,[x[0] for x in gaps],[x[1] for x in gaps])/1e9 for k,v in work.items()}
    gap_ns=sum(b-a for a,b in gaps)
    require(sum(explained.values())<=gap_ns/1e9+1e-8,'互斥前台开销被重复解释')
    services=[]
    fair=[]
    for i,line in enumerate(lines,1):
        m=re.search(r'RR公平队列服务：Zone=(\d+)，.*服务时点纳秒=(\d+)',line)
        if m:fair.append((i,int(m[1]),int(m[2])))
    for index,(line,zone,ns) in enumerate(fair):
        if not worst[4]<=ns<=worst[5]:continue
        next_line=fair[index+1][0] if index+1<len(fair) else len(lines)+1
        matched=[tx for tx in report['事务'] if tx['类型']=='RR' and tx['Zone']==zone
                 and line<tx['开始行']-1<next_line]
        services.append(dict(服务行=line,Zone=zone,服务纳秒=ns,
            维护=[dict(迁移字节=tx['迁移字节'],复制文件数=len(tx['复制']),开始行=tx['开始行']-1,结束行=tx['结束行']-1) for tx in matched]))
    strict=[r for r in phases if r[0]==11 and r[1]<worst[5] and r[2]>worst[4]]
    creations=[e for e in events if e['event']=='table_file_creation']
    flush=parse_flush((root/'run.log').read_text())
    flush_summary={name:dict(累计秒=sum(j['分段秒'].get(name,0) for j in flush),
                            最大秒=max(j['分段秒'].get(name,0) for j in flush))
                   for name in ('构建','日志刷新','目录同步','数据库锁')}
    build_windows=merge([(j['时点纳秒']['构建前'],j['时点纳秒']['构建后']) for j in flush])
    allocation=[(r[1],r[2]) for r in phases if r[0]==12]
    flush_summary['分配恢复与构建重叠秒']=overlap(allocation,
        [a for a,_ in build_windows],[b for _,b in build_windows])/1e9
    return dict(复制来源={k:dict(v) for k,v in copies.items()},在线热文件实际复制={str(k):dict(v) for k,v in hot_count.items()},
        分位点对应实际Get=at_percentile,慢读分组=tail,原生Flush分段=flush_summary,
        原生工作=dict(Flush=sum(e['event']=='flush_started' for e in events),
            Compaction=sum(e['event']=='compaction_started' for e in events),
            SST创建字节=sum(e['file_size'] for e in creations),
            按来源创建字节={k:sum(e['file_size'] for e in creations if jobs[e['job']]==k) for k in ('Flush','Compaction')}),
        请求间隔=dict(总秒=gap_ns/1e9,慢生成值覆盖秒=explained.get(0,0),
            慢请求记录覆盖秒=explained.get(1,0),慢原生统计覆盖秒=explained.get(2,0),
            慢阶段输出覆盖秒=explained.get(3,0),未由慢记录解释秒=gap_ns/1e9-sum(explained.values())),
        最慢Get=dict(序号=worst[0],Key=worst[3],开始纳秒=worst[4],结束纳秒=worst[5],毫秒=(worst[5]-worst[4])/1e6,
            严格RR重叠毫秒=overlap([(r[1],r[2]) for r in strict],[worst[4]],[worst[5]])/1e6,服务=services),
        边界='工作来源按原生job和文件创建关联；在线H不是物理RC。慢前台事件只保存>=1ms，未解释部分含快操作/观测/调度，不能虚构成维护收益。RR事务行号按派生审计前缀校正。')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    require(not args.output.exists(),'不覆盖归因结果');r=args.results
    state=json.loads((r/'状态.json').read_text());require(state['完整实验完成'] and state['当前客机PID'] is None,'实验未完成退出')
    result={t:explain(r/'逐臂采证'/t,json.loads((r/(t+'-核验.json')).read_text())) for t in ('p1','m1')}
    args.output.mkdir(parents=True)
    payload=dict(来源=str(r),源码SHA256=digest(Path(__file__)),各臂=result,
        读取成本={t:json.loads((r/(t+'-读取成本核验.json')).read_text()) for t in result})
    (args.output/'逐指标归因.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
