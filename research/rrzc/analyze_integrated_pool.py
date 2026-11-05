"""完整机制的直接业务对照；不以RR减少代替总维护成本，也不预设正收益。"""
import csv
import json
from pathlib import Path
import re
import tempfile
from analyze_reproducibility import request_rows,request_comparison,initial,accounting
from analyze_pool_dbbench import SEMANTIC,space_handoff
from analyze_calibration import background_overlap
from analyze_maintenance_cover import decisions
from audit_colocation_transactions import audit
from cache_config_guard import check as cache_check
from ycsb_af import digest,require

READS=786432;WRITES=1474560;REQUESTS=READS+WRITES


def planned(windows=12):
    for window in range(windows):
        for part in range(2):
            reads=0
            for n in range(61440):
                yield 1,(window%6)*65536+4096+n
                while reads<(n+1)*32768//61440:
                    yield 0,(part*32768+reads)%4096;reads+=1


def arm(root,greedy,scorer,pool_k=None,windows=12,decision_reader=None,concurrent=False):
    require(windows in (12,24),'只核验原12或持续24窗口')
    expected_counts=[windows*65536,windows*122880];requests=sum(expected_counts)
    require((root/'wrapper.exit').read_text().strip()=='0','业务或内容校验失败')
    log=(root/'run.zenfs.log').read_text();run=(root/'run.log').read_text()
    require('[ERROR]' not in log and '候选范围配置：版本=1，合法=1，扩大=0' in log,'原资格或线程状态异常')
    expected_pool=int(not greedy) if pool_k is None else pool_k
    require(expected_pool in (0,1),'池规模不合法')
    require(('在线协同池：K=1' in log)==bool(expected_pool),'实际池配置不符')
    require(re.search(r'readseq\s*:.*1048576\s+operations',(root/'verify.log').read_text()),'完整读回缺失')
    expected=iter(planned(windows));counts=[0,0];first=last=None
    for i,row in enumerate(request_rows(root/'请求提交.bin')):
        wanted=next(expected,None)
        require(wanted is not None and row[:4]==(i,wanted[0],counts[wanted[0]],wanted[1]),'请求与预注册首差：'+str(i))
        counts[row[1]]+=1
        if first is None:first=row[4]
        last=row[6]
    require(next(expected,None) is None and counts==expected_counts,'固定工作量未完成')
    require(len(re.findall('SST生命周期阶段：序号=',run))==windows*10,'维护检查点缺失')
    with tempfile.TemporaryDirectory(prefix='rrzc-integrated-ledger-') as tmp:
        p=Path(tmp);(p/'zenfs.log').write_text('受控放置业务开始：派生窗\n'+log+'\n受控放置业务截止：派生窗\n')
        tx=audit(p,allow_retired_rollback=concurrent);selected=decisions(p,scorer) if decision_reader is None else decision_reader(p,scorer)
    require(all(x['字段']['执行Greedy']==int(greedy) for x in selected),'实际选区开关不符')
    require(tx['维护账目']==accounting(root),'复制及汇总账目不同')
    before=json.loads((root/'strict.before.json').read_text());after=json.loads((root/'strict.business.json').read_text())
    require(after['历史最高RC']<=1024,'严格RC安全边界异常')
    lat=[]
    for row in re.findall(r'RRZC调用延迟：操作=(READ|WRITE)，次数=(\d+)，平均纳秒=([\d.]+)，P99纳秒=(\d+)，P99.9纳秒=(\d+)，P99.99纳秒=(\d+)',run):
        role,n,mean,p99,p999,p9999=row
        lat.append(dict(操作=role,次数=int(n),平均纳秒=float(mean),P99纳秒=int(p99),P999纳秒=int(p999),P9999纳秒=int(p9999)))
    require({r['操作']:r['次数'] for r in lat}==dict(zip(('READ','WRITE'),expected_counts)),'API统计不完整')
    start,end=(int((root/n).read_text()) for n in ('run.start.ns','run.end.ns'))
    require(start<=first<=last<=end,'计时边界不闭合')
    finishes=[(int(n),int(ok)) for n,ok in re.findall(
        r'公共普通分配Finish代价：[^\n]*牺牲尾部字节=(\d+)，成功=(\d+)',log)]
    zones=json.loads((root/'zones.business.json').read_text())['zone_list']
    return dict(通过=True,维护=tx['维护账目'],事务=tx['事务'],逐轮选择=selected,
                改选次数=sum(x['改选'] for x in selected),API延迟=lat,
                计时=dict(请求首尾秒=(last-first)/1e9,请求窗口吞吐ops=requests/((last-first)/1e9),
                          整阶段秒=(end-start)/1e9,整阶段吞吐ops=requests/((end-start)/1e9)),
                在线H晋升次数=run.count('H=1，窗口读取='),在线H降级次数=run.count('H=0，窗口读取='),
                资源=dict(普通成功Finish次数=sum(ok for _,ok in finishes),
                          普通Finish尾损字节=sum(n for n,ok in finishes if ok),
                          RR源封闭尾部字节=sum(map(int,re.findall(r'RR 开始：[^\n]*封闭剩余容量 (\d+) 字节',log))),
                          末端OpenZone数=sum(z['state'] in ('IMP_OPENED','EXP_OPENED') for z in zones),
                          末端ActiveZone数=sum(z['state'] in ('IMP_OPENED','EXP_OPENED','CLOSED') for z in zones)),
                末端空间=json.loads((root/'space.business.json').read_text()),
                缓存=cache_check((root/'run.rocksdb.log').read_text(),1),
                后台任务=background_overlap((root/'run.rocksdb.log').read_text()),空间交接=space_handoff(log),
                设备增量={k:after[k]-before[k] for k in ('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取')})


def finish(root,output):
    require(not output.exists(),'不覆盖分析结果')
    state=json.loads((root/'状态.json').read_text());archive=json.loads((root/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None,'未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    rows=[];lat=[];reports={};starts={}
    for tag in ('p1','p2','m1','m2'):
        r=json.loads((root/(tag+'-核验.json')).read_text());reports[tag]=r
        rows.append(dict(臂=tag,**r['维护'],**r['计时'],**r['资源'],改选次数=r['改选次数'],H晋升次数=r['在线H晋升次数'],
                         **{k:v for k,v in r['末端空间'].items() if k!='Zone'}))
        lat.extend(dict(臂=tag,**x) for x in r['API延迟'])
        if tag!='p1':
            a=initial(root/'逐臂采证/p1',root/'逐臂采证'/tag);b=request_comparison(root/'逐臂采证/p1',root/'逐臂采证'/tag)
            require(all(a[k]['相同'] for k in SEMANTIC) and b['逻辑提交相同'],'跨臂起点或请求不同')
            starts[tag]=dict(起点=a,请求=b)
    output.mkdir(parents=True)
    for name,items in (('维护空间与吞吐.csv',rows),('API延迟.csv',lat)):
        with (output/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,list(items[0]));writer.writeheader();writer.writerows(items)
    repeats={n:json.loads((root/(n+'重复核验.json')).read_text()) for n in ('Greedy','机制')}
    result=dict(通过=True,来源=str(root),各臂=reports,跨臂=starts,重复=repeats,
                边界='真实RocksDB定制db_bench，受控单前台；H在线重复读识别，W为Flush来源代理；K固定1，无精确寿命预测。结果含零收益/负收益，正式统计需独立种子配对。')
    (output/'完整对照核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result
