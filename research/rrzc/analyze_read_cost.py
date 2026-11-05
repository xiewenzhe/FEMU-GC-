"""按真实Get序号闭合读锁等待、读调用及其他Get开销；只读原始证据。"""
import struct
from collections import Counter
from analyze_reproducibility import request_rows
from analyze_phase_time import merge,overlap
from ycsb_af import require,digest

WORK=('生成值','延迟向量与请求记录','原生统计','阶段输出')


def analyze(root):
    wanted=(r for r in request_rows(root/'请求提交.bin') if r[1]==0)
    total=count=wait=body=io=commands=0;front_tid=None;calls=[];slow=[];work=[];totals={}
    with (root/'读取成本.bin').open('rb') as f:
        require(f.read(16)==b'RRZCRD01'+bytes(8),'读成本头异常或缓冲丢弃')
        while raw:=f.read(80):
            require(len(raw)==80,'读成本尾部缺失');r=struct.unpack('<10Q',raw)
            if r[0]==1:
                q=next(wanted,None);require(q is not None,'Get成本多于请求')
                _,seq,key,tid,begin,end,w,b,i,c=r
                require((seq,key,begin,end)==(q[0],q[3],q[4],q[5]),'Get成本与请求不一致')
                require(w+b<=end-begin and i<=b,'Get成本嵌套或越界，禁止相加')
                require(front_tid is None or front_tid==tid,'不是单前台Get线程')
                front_tid=tid;count+=1;total+=end-begin;wait+=w;body+=b;io+=i;commands+=c
                if end-begin>1000000:slow.append((begin,end,w,b))
            elif r[0]==2:
                require(r[4]<=r[5]<=r[6] and r[7]<=r[6]-r[5],'读调用计时倒退')
                calls.append(r)
            elif r[0]==3:work.append(r)
            elif r[0]==4:
                totals[WORK[r[1]]]=dict(次数=r[2],全方法累计秒=r[3]/1e9,最大秒=r[4]/1e9)
            else:raise ValueError('读成本未知类型')
    require(next(wanted,None) is None and count==786432,'Get成本未覆盖全请求')
    bg=[r for r in calls if r[3]!=front_tid]
    waits=merge([(r[4],r[5]) for r in calls if r[3]==front_tid])
    holds=merge([(r[5],r[6]) for r in bg])
    covered=overlap(waits,[x[0] for x in holds],[x[1] for x in holds])
    sizes=Counter((r[2],r[8]) for r in bg)
    size_info=[]
    for (size,ncmd),n in sizes.most_common(12):
        matching=[r for r in bg if (r[2],r[8])==(size,ncmd)]
        size_info.append(dict(字节=size,每调用命令数=ncmd,次数=n,
            平均读调用毫秒=sum(r[6]-r[5] for r in matching)/n/1e6,
            平均命令累计毫秒=sum(r[7] for r in matching)/n/1e6))
    return dict(通过=True,Get数=count,前台Linux线程=front_tid,
        Get累计秒=total/1e9,锁等待秒=wait/1e9,读调用秒=body/1e9,
        其中同步命令累计秒=io/1e9,读调用其他秒=(body-io)/1e9,
        Get其他秒=(total-wait-body)/1e9,前台实际命令数=commands,
        平均微秒=dict(Get=total/count/1000,锁等待=wait/count/1000,读调用=body/count/1000,
            命令=io/count/1000,其他=(total-wait-body)/count/1000),
        慢Get数=len(slow),慢Get锁等待秒=sum(r[2] for r in slow)/1e9,
        慢或大读取已记录前台等待并集秒=sum(b-a for a,b in waits)/1e9,
        其中被后台读持锁区间覆盖秒=covered/1e9,后台大或慢调用=len(bg),后台读块=size_info,
        前台方法成本=totals,慢前台记录数=len(work),
        慢前台开销=[dict(类别=WORK[k],次数=sum(r[1]==k for r in work),
            累计秒=sum(r[5]-r[4] for r in work if r[1]==k)/1e9) for k in range(4)],
        输入SHA256={n:digest(root/n) for n in ('请求提交.bin','读取成本.bin')},
        边界='读调用包含命令、拷贝和统计；命令时间包含内核和模拟设备，不是纯NAND。后台保留>=64KiB或>=1ms调用；前台Get全部记录。方法成本含首尾少量请求窗外操作，不直接并入请求窗口。')
