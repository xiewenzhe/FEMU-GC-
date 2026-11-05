"""复用已验证的客机归档及清理，只注入独立轮转分析器和请求核验。"""
from dbbench_hotspot_suite import worker as original_worker,cpu_sets
from dbbench_rotating_suite import READS,WRITES,trace
from run_dbbench_parallel_host import capacity
from ycsb_af import require


def audit_trace(root,data,stage):
    text=(root/'run.log').read_text();rows=trace(text)
    rounds=2 if stage=='smoke' else 24
    reads=2048 if stage=='smoke' else READS;writes=2048 if stage=='smoke' else WRITES
    require(len(rows)==2*rounds,'实际完成批次数不符')
    saved=[]
    for role,total in (('READ',reads),('WRITE',writes)):
        for n in range(rounds):
            count,base,checksum=rows[(role,n)]
            expected=((n//(1 if stage=='smoke' else 4))%(1 if stage=='smoke' else 16))*65536
            require(count==(n+1)*(total//rounds) and base==expected,'实际批次累计数或热点切换不符')
            saved.append([role,n,count,base,checksum])
    data['批次请求摘要']=saved
    data['负载说明']='定制轮转db_bench；固定批次汇合，无速率限制；旧ReadRandomRate的1毫秒等待不在上轮实际路径'
    data['延迟口径']='Get/Put函数调用单独计时，不含批次等待和生成键；包含调用中的设备维护及阻塞'


def worker(args,password,output,stage,mode):
    budget=capacity();cores=cpu_sets(budget['逻辑CPU'],2)[0]
    return original_worker(args,password,output,stage,mode,0,cores,read_count=READS,checkpoints=True,
        smoke_records=65536,smoke_reads=2048,analyzer_name='analyze_dbbench_rotating.py',
        analyze_hook=audit_trace,layout_read_keys=4096)
