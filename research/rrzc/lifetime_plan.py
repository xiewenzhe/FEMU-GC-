"""独立核验寿命测试的固定请求；只用于测试，不输入生产选择器。"""
from collections import defaultdict
from analyze_competition_workload import expected_reads as original_reads,planned_files,MIB
from analyze_v1_shadow_stream import require

LABEL='lifetime-20261016'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'

def early(g,k):return (g%3==0 and k==0) or (g%3==2 and k>0)
def name(g,k):return f'/rrzc-stream/g{g}.{("cold","warm","hot")[k]}.anchor.sst'
def expected_reads():
    return [r for r in original_reads(180) if not early(r[1],r[2]) or r[0]-r[1]*30*10**9<67*10**9]
def early_events(retire_delay=0):
    require(retire_delay in (0,20),'未登记的退役延迟')
    return [(g*30+68+retire_delay,g,k) for g in range(6) for k in range(3) if early(g,k) and g*30+68+retire_delay<180]
def final_files(retire_delay=0):
    require(retire_delay in (0,20),'未登记的退役延迟')
    result={}
    for g in range(2,6):
        for n,v in planned_files(g).items():
            if '.anchor.' in n:
                k=('cold','warm','hot').index(n.split('.')[-3])
                if early(g,k) and g*30+68+retire_delay<180:continue
                result[n]=v*MIB
            elif g==5:result[n]=v*MIB
    result.update({f'/rrzc-stream/filler{i}.bin':256*MIB for i in range(18)})
    return result
def check_trace(ops,retire_delay=0):
    serials=[0,0,0];reads=[];writes=defaultdict(list);deletes=[]
    for r in ops:
        t=int(r['线程']);g=int(r['代号']);n=r['文件']
        require(t in (0,1,2) and int(r['序号'])==serials[t] and r['成功']=='1','请求序号或结果错误')
        serials[t]+=1
        if r['操作']=='读4KiB':
            k=2 if '.hot.' in n else 1 if '.warm.' in n else 0
            require(t==1 and n==name(g,k) and int(r['字节'])==4096,'读取文件错误')
            reads.append((g,k,r['阶段'],int(r['偏移'])))
        elif r['操作']=='写1MiB' and g>=0:
            require(t==0 and int(r['字节'])==MIB,'写请求错误');writes[g].append((n,int(r['偏移'])))
        if t==2 and r['操作']=='删除':deletes.append((g,n))
    require(reads==[r[1:] for r in expected_reads()],'失效场景的固定读计划不完整')
    require(set(writes)==set(range(6)),'写代际缺失')
    for g,actual in writes.items():
        expected={(n,i*MIB) for n,v in planned_files(g).items() for i in range(v)}
        require(len(actual)==768 and set(actual)==expected,'每代写入不符')
    require(deletes==[(g,name(g,k)+'.retired.bin') for _,g,k in early_events(retire_delay)],'早退役删除不符')
    # 所有计划删除必须在最后业务读及读句柄关闭之后，且不得早于预设时刻。
    for due,g,k in early_events(retire_delay):
        n=name(g,k);last=max(int(r['开始纳秒'])+int(r['延迟纳秒']) for r in ops if r['文件']==n and r['操作']=='读4KiB')
        delete=next(r for r in ops if r['线程']=='2' and r['文件']==n+'.retired.bin' and r['操作']=='删除')
        require(int(delete['开始纳秒'])>=max(last,due*10**9),'删除早于最后读取或预设时间')
    return dict(读请求数=len(reads),业务写入MiB=sum(map(len,writes.values())),预设早退役数=len(deletes))
