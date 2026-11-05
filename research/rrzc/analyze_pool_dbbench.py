"""真实db_bench的筛查与重复准入；未通过时不发布模型收益。"""
import json
from pathlib import Path
import re
import tempfile
from analyze_reproducibility import (request_comparison,initial,accounting,collect,compare_stream,ROCK_FIELDS,MARKERS)
from analyze_calibration import timing,background_overlap
from cache_config_guard import check as cache_check
from analyze_maintenance_cover import decisions
from audit_colocation_transactions import audit

SEMANTIC=('zones.before.json','space.before.json','装载-0.sst.csv','装载-0.extent.csv',
          '布局-0.sst.csv','布局-0.extent.csv','全部物理块身份','全部物理块RC','全部物理块近期增量及统计标志')


def space_handoff(log):
    markers=('受控空间请求：','受控空间维护完成：','受控空间确认：')
    rows=[[int(n) for n in re.findall(m+r'序号=(\d+)',log)] for m in markers]
    assert rows[0]==rows[1]==rows[2]==list(range(1,len(rows[0])+1)),'空间请求/完成/确认未闭合'
    emergencies=[line for line in log.splitlines() if '受控GC紧急检查：' in line]
    assert all('空间请求=' in line for line in emergencies),'仍存在瞬时等待数轮询触发'
    assert [int(re.search(r'空间请求=(\d+)',line).group(1)) for line in emergencies]==rows[0]
    for n in rows[0]:
        done=log.index('受控空间维护完成：序号='+str(n)+'，')
        ack=log.index('受控空间确认：序号='+str(n)+'，')
        assert done<ack,'分配确认早于整轮维护完成'
    return dict(通过=True,请求数=len(rows[0]),序号=rows[0],轮询紧急检查数=0,
        边界='零请求仅证明未发生压力交接，不代表已验证实际压力路径' if not rows[0] else '实际压力交接逐笔闭合')


def arm(root,greedy,binary,reads=786432,writes=589824,timing_reader=timing):
    assert (root/'wrapper.exit').read_text().strip()=='0'
    assert json.loads((root/'起始核验.json').read_text())['通过']
    run=(root/'run.log').read_text();log=(root/'run.zenfs.log').read_text()
    assert '[ERROR]' not in log and '候选范围配置：版本=1，合法=1，扩大=0' in log
    assert '协同池配置：K=1' in log and '受控GC批次检查：启用=1，原启动水平=20%' in log
    cache=cache_check((root/'run.rocksdb.log').read_text(),1)
    overlap=background_overlap((root/'run.rocksdb.log').read_text());assert overlap['重叠次数']==0
    assert re.search(r'readseq\s*:.*917504\s+operations', (root/'verify.log').read_text()),'最终扫描请求数未确认'
    # 计量窗是run进程从打开到关闭，含尾部Flush；临时边界只是派生解析夹具，不改原日志。
    with tempfile.TemporaryDirectory(prefix='rrzc-db-ledger-') as temp:
        derived=Path(temp);(derived/'zenfs.log').write_text('受控放置业务开始：派生run进程窗\n'+log+'\n受控放置业务截止：派生run进程窗\n')
        choices=decisions(derived,binary);tx=audit(derived)
    assert all(x['字段']['执行Greedy']==int(greedy) for x in choices)
    counters=[json.loads((root/n).read_text()) for n in ('strict.before.json','strict.business.json')]
    assert counters[1]['历史最高RC']<=1024
    keys=('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')
    delta={k:counters[1][k]-counters[0][k] for k in keys};assert min(delta.values())>=0
    latencies=[]
    for row in re.findall(r'RRZC调用延迟：操作=(READ|WRITE)，次数=(\d+)，平均纳秒=([\d.]+)，P99纳秒=(\d+)，P99.9纳秒=(\d+)，P99.99纳秒=(\d+)',run):
        role,n,mean,p99,p999,p9999=row
        latencies.append(dict(操作=role,次数=int(n),平均纳秒=float(mean),P99纳秒=int(p99),P999纳秒=int(p999),P9999纳秒=int(p9999)))
    assert {r['操作']:r['次数'] for r in latencies}=={'READ':reads,'WRITE':writes}
    assert tx['维护账目']==accounting(root)
    sync=None
    if '受控空间请求：' in log or '空间请求=' in log:sync=space_handoff(log)
    return dict(通过=True,维护=tx['维护账目'],事务=tx['事务'],逐轮选择=choices,空间交接=sync,
        改选次数=sum(x['改选'] for x in choices),计时=timing_reader(root),API延迟=latencies,
        缓存配置=cache,后台任务=overlap,设备窗口增量=delta,
        末端空间=json.loads((root/'space.business.json').read_text()),
        边界='run进程含打开关闭及尾部Flush；API窗口另列；无逐请求设备摘要，不能声称逐条物理命令完全同步')


def repeat_guard(a,b,request_count=1376256):
    req=request_comparison(a,b);start=initial(a,b)
    xa,xb=collect(a),collect(b);events={}
    for kind in ROCK_FIELDS:
        events['RocksDB/'+kind]=compare_stream(*[[e for e in x[0] if e['事件']==kind] for x in (xa,xb)])
    for marker in MARKERS:
        events['ZenFS/'+marker]=compare_stream(*[[e for e in x[1] if e['事件']==marker[:-1]] for x in (xa,xb)])
    ledger=accounting(a)==accounting(b)
    terminal=all(json.loads((a/n).read_text())==json.loads((b/n).read_text()) for n in ('space.business.json','zones.business.json'))
    delta=lambda p:{k:json.loads((p/'strict.business.json').read_text())[k]-json.loads((p/'strict.before.json').read_text())[k]
        for k in ('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')}
    counters=delta(a)==delta(b)
    ok=(req['逻辑提交相同'] and req['完整成功请求数']==[request_count]*2 and all(start[k]['相同'] for k in SEMANTIC)
        and all(x['相同'] for x in events.values()) and ledger and terminal and counters)
    return dict(通过=ok,请求=req,起点=start,事件首差=events,维护账目相同=ledger,末端相同=terminal,
        设备增量相同=counters,限制='未采集逐请求设备摘要；仅实际分层状态、请求及事件/计数核验')


def analyze(output,binary):
    s=json.loads((output/'状态.json').read_text());a=json.loads((output/'阶段归档.json').read_text())
    assert s['当前客机PID'] is None and a['完整'] and a['业务成功'] and not a['客机仍存活']
    source=output/s['标签'];groups={}
    for mode,tags in (('greedy',('p1','p2')),('model',('m1','m2'))):
        reports=[arm(source/tag,mode=='greedy',binary) for tag in tags]
        repeated=repeat_guard(*(source/tag for tag in tags));assert repeated['通过'],str(repeated)
        groups[mode]=dict(各臂=reports,重复=repeated)
    req=request_comparison(source/'p1',source/'m1');assert req['逻辑提交相同']
    starts=initial(source/'p1',source/'m1');assert all(starts[k]['相同'] for k in SEMANTIC)
    result=dict(通过=True,各组=groups,起点=starts,逻辑输入=req,
        限制='真实RocksDB定制db_bench，准备期显式H/W标签；新Compaction输出不在线分类；受控调度非正常并发性能')
    p=output/'真实DB核验.json';assert not p.exists();p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result
