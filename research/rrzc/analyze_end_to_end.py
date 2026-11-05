"""逐请求核验应用产生—入队—执行—最终确认时间，不以总耗时摊算延迟。"""
from array import array
from collections import Counter
import csv
import json
import mmap
from pathlib import Path
import re
import struct
from ycsb_af import require,digest

ROW=struct.Struct('<10Q')


def analyze(root,smoke=False):
    api_path=root/('计时小测请求.bin' if smoke else '请求提交.bin')
    done_path=root/('应用小测完成.bin' if smoke else '应用完成.bin')
    log_path=root/('计时客户端小测.log' if smoke else 'run.log')
    log=log_path.read_text()
    batches={}
    pattern=r'应用写批次完成：批次=(\d+)，写数=(\d+)，首请求=(\d+)，末请求=(\d+)，Flush开始纳秒=(\d+)，Flush返回纳秒=(\d+)，完成纳秒=(\d+)'
    for values in re.findall(pattern,log):
        batch,n,first,last,begin,end,complete=map(int,values)
        require(batch not in batches and first<=last and begin<=end<=complete,'批提交日志重复或倒退')
        batches[batch]=(n,first,last,begin,end,complete)
    require(batches,'没有实际同步Flush完成证据')
    distributions=[array('Q'),array('Q')];api_dist=[array('Q'),array('Q')]
    sums=[Counter(),Counter()];counts=Counter();actual_batches=Counter();ranges={};first_created=None;last_completed=0
    with api_path.open('rb') as af,done_path.open('rb') as df:
        with mmap.mmap(af.fileno(),0,access=mmap.ACCESS_READ) as api:
            require(api[:8]==b'RRZCREQ1' and (len(api)-8)%56==0,'原API记录格式异常')
            n=(len(api)-8)//56;seen=bytearray(n)
            require(df.read(8)==b'RRZCE201','应用完成记录格式异常')
            capacity,batch_size=struct.unpack('<2Q',df.read(16))
            require((capacity,batch_size)==((64,32) if smoke else (4096,2048)),'应用队列或批大小不符')
            for index in range(n):
                raw=df.read(80);require(len(raw)==80,'应用完成记录提前结束')
                seq,role,key,created,enqueued,dispatch,begin,end,complete,batch=ROW.unpack(raw)
                require(seq<n and role in (0,1) and not seen[seq],'完成序号非法、重复或操作类型错误')
                seen[seq]=1
                q=struct.unpack_from('<7Q',api,8+seq*56)
                require((q[0],q[1],q[3],q[4],q[5])==(seq,role,key,begin,end),'应用完成未对应同一条真实API')
                require(created<=enqueued<=dispatch<=begin<=end<=complete,'应用请求时间线倒退')
                if role:
                    require(batch in batches,'写完成没有实际Flush批次')
                    size,first,last,fb,fe,fc=batches[batch]
                    require(first<=seq<=last and end<=fb<=fe<=fc==complete,'写请求在实际Flush完成前确认')
                    actual_batches[batch]+=1
                    low,high=ranges.get(batch,(seq,seq));ranges[batch]=(min(low,seq),max(high,seq))
                else:require(batch==0,'读请求不能冒充持久化批次完成')
                pieces=dict(入队背压=enqueued-created,队列等待=dispatch-enqueued,
                    执行准备=begin-dispatch,API=end-begin,API后完成等待=complete-end)
                require(sum(pieces.values())==complete-created,'逐请求延迟分账不闭合')
                counts[role]+=1;sums[role].update(pieces)
                distributions[role].append(complete-created);api_dist[role].append(end-begin)
                first_created=created if first_created is None else min(first_created,created)
                last_completed=max(last_completed,complete)
            require(not df.read(1) and all(seen),'应用完成记录尾部多余或有遗漏')
    require(counts==({0:256,1:480} if smoke else {0:786432,1:1474560}),'应用读写完成数不符')
    require(actual_batches==Counter({k:v[0] for k,v in batches.items()}),'实际每批确认数不闭合')
    require(sorted(batches)==list(range(1,len(batches)+1)),'批次序号不连续')
    previous=-1
    for batch,values in sorted(batches.items()):
        size,first,last,*_=values
        require(0<size<=batch_size and ranges[batch]==(first,last) and previous<first,
            '批确认请求范围重叠或与实际写记录不符')
        if not smoke:require(size==batch_size,'完整实验存在未预注册的部分写批')
        previous=last
    metrics=[]
    for role,label in ((0,'READ'),(1,'WRITE')):
        values=sorted(distributions[role]);count=len(values)
        q=lambda p:values[(count*p+999999)//1000000-1]/1e6
        metrics.append(dict(操作=label,次数=count,平均毫秒=sum(values)/count/1e6,
            P99毫秒=q(990000),P999毫秒=q(999000),P9999毫秒=q(999900),最大毫秒=values[-1]/1e6,
            平均入队背压毫秒=sums[role]['入队背压']/count/1e6,
            平均队列等待毫秒=sums[role]['队列等待']/count/1e6,
            平均执行准备毫秒=sums[role]['执行准备']/count/1e6,
            平均API毫秒=sums[role]['API']/count/1e6,
            平均API后完成等待毫秒=sums[role]['API后完成等待']/count/1e6))
    return dict(通过=True,队列容量=capacity,写批大小=batch_size,完成请求数=sum(counts.values()),
        实际同步写批次=len(batches),应用产生至最终完成秒=(last_completed-first_created)/1e9,
        应用完成吞吐ops=sum(counts.values())/((last_completed-first_created)/1e9),指标=metrics,
        输入SHA256={p.name:digest(p) for p in (api_path,done_path,log_path)},
        口径='请求准备入队→成功完成确认；读在Get/校验后完成；写在实际同步Flush成功后同批确认。'
             '无QPS限速，有界队列4096，产生包括队列满背压；写前Put仅内存返回，不据此完成。'
             'RocksDB/ZenFS及模拟设备完成语义，不证明模拟NAND断电持久性；不是任意负载通用延迟。')


def finish(root):
    output=root/'应用端到端分析';output.mkdir()
    reports={t:json.loads((root/(t+'-应用端到端核验.json')).read_text()) for t in ('p1','m1')}
    rows=[dict(臂=t,**row) for t,p in reports.items() for row in p['指标']]
    with (output/'端到端平均与长尾.csv').open('x',newline='') as f:
        w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
    (output/'逐请求完成核验.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
    lines=['# 应用请求产生至最终完成：平均与长尾','',reports['p1']['口径'],'',
        '|指标，毫秒|普通放置READ|协同放置READ|普通放置WRITE|协同放置WRITE|',
        '|---|---:|---:|---:|---:|']
    for k in ('平均毫秒','P99毫秒','P999毫秒','P9999毫秒','最大毫秒'):
        values=[reports[t]['指标'][i][k] for i,t in ((0,'p1'),(0,'m1'),(1,'p1'),(1,'m1'))]
        lines.append('|'+k+'|'+'|'.join(f'{v:.6f}' for v in values)+'|')
    lines+=['','每条请求均核对API身份与时间戳；每笔写确认必须对应实际Flush完成日志。'
        '读写可完成乱序，按原请求序号核验不漏不重。平均分段相加闭合，长尾由真实单条延迟排序，未摊分Flush耗时。'
        '队列上限和批大小会影响结果；本轮完成语义与旧Put/API实验不同，不能直接合并数值。']
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    return reports
