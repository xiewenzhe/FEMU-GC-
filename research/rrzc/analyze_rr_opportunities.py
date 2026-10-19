"""离线核对回收资格与近期RR风险是否重叠；不修改模型，不补造维护机会。"""
import hashlib,json
from collections import Counter,defaultdict
from pathlib import Path
from analyze_pair_rate import audit as pair_audit,LABEL
from analyze_stream import rows
from analyze_maintenance_overlap import RR_START
from analyze_v1_shadow_stream import fields,require

SECOND=10**9
CAPACITY=256<<20
RECLAIM=192<<20
THRESHOLD=4096

def stable(row):
    return (row['统计稳定']=='1' and int(row['窗口标志'])&1 and
        row['设备代际前']==row['设备代际后'] and
        row['主机代际前']==row['主机代际后'] and
        row['主机序号前']==row['主机序号后'] and int(row['主机序号后'])%2==0 and
        0<=int(row['查询结束纳秒'])-int(row['查询开始纳秒'])<=2*SECOND)

def same_block(a,b):
    return all(a[k]==b[k] for k in ('Zone','会话高位','会话低位','设备代际后',
        '主机代际后','主机序号后','块编号'))

def adjacent_rate(old,current):
    """离线相邻快照估速，不冒充生产双历史端点建议；只统计同一物理块。"""
    out=dict(可知=False,真实增量=None,间隔纳秒=None,预测纳秒=None,原因='缺前点')
    if old is None:return out
    if not stable(old) or not stable(current) or not same_block(old,current):
        out['原因']='查询、代际或最大块身份不稳定';return out
    if int(old['查询结束纳秒'])>=int(current['查询开始纳秒']):
        out['原因']='前查询未早于当前查询';return out
    span=int(current['设备快照纳秒'])-int(old['设备快照纳秒'])
    delta=int(current['RC'])-int(old['RC'])
    if not SECOND//2<=span<=2*SECOND or delta<0:
        out['原因']='间隔超界或RC回退';return out
    rc=int(current['RC'])
    eta=0 if rc>=THRESHOLD else ((THRESHOLD-rc)*span+delta-1)//delta if delta else None
    return dict(可知=True,真实增量=delta,间隔纳秒=span,预测纳秒=eta,
        原因='同块相邻快照真实增量' if delta else '同块窗口无读取')

def snapshot_audit(zones,mapping):
    parts=defaultdict(list)
    for p in mapping:parts[p['批次'],p['Zone']].append(p)
    previous={};seen=set();unknown=Counter();counts=Counter();risk=[]
    for number,z in enumerate(zones,2):
        key=z['批次'],z['Zone'];require(key not in seen,'重复区快照');seen.add(key)
        require(int(z['容量字节'])==CAPACITY and 0<=int(z['有效字节'])<=CAPACITY,'快照几何或有效量异常')
        old=previous.get(z['Zone']);previous[z['Zone']]=z
        if z['阶段']!='1' or int(z['有效字节'])==0:continue
        counts['混合阶段非空区快照']+=1
        rate=adjacent_rate(old,z)
        if not rate['可知']:unknown[rate['原因']]+=1;continue
        counts['相邻快照速率可知']+=1
        if rate['预测纳秒'] is None or rate['预测纳秒']>10*SECOND:continue
        v=int(z['有效字节']);p=parts.get(key,[])
        full=z['剩余字节']=='0';budget=CAPACITY-v>=RECLAIM
        coverage=(sum(int(f['区内有效字节']) for f in p)==v==int(z['映射覆盖字节']) and
                  all(f['文件'].endswith('.sst') for f in p))
        group='阈值前近期紧迫' if int(z['RC'])<THRESHOLD else '当前已经达阈值'
        failures=[]
        if not full:failures.append('不是满Zone')
        if not budget:failures.append('未达到192MiB回收预算')
        if not coverage:failures.append('SST覆盖未确认完整')
        counts[group]+=1
        for condition,name in ((full,'满区'),(budget,'预算满足'),(full and budget,'满区且预算满足'),
                               (full and budget and coverage,'基础资格观察')):
            if condition:counts[group+'_'+name]+=1
        risk.append(dict(CSV行=number,批次=z['批次'],Zone=z['Zone'],会话=z['会话高位']+'/'+z['会话低位'],
            设备代际=z['设备代际后'],主机代际=z['主机代际后'],块编号=z['块编号'],
            查询开始纳秒=int(z['查询开始纳秒']),分组=group,有效字节=v,当前RC=int(z['RC']),
            满区=full,回收预算满足=budget,SST覆盖完整=coverage,失败条件=failures,
            离线估速=rate))
    groups={}
    for name in ('阈值前近期紧迫','当前已经达阈值'):
        subset=[r for r in risk if r['分组']==name]
        groups[name]=dict(重复快照数=len(subset),
            区代际数=len({(r['Zone'],r['会话'],r['设备代际'],r['主机代际']) for r in subset}),
            满区数=counts[name+'_满区'],预算满足数=counts[name+'_预算满足'],
            满区且预算满足数=counts[name+'_满区且预算满足'],基础资格观察数=counts[name+'_基础资格观察'])
    return dict(计数=dict(counts),分组=groups,未知原因=dict(unknown),风险快照=risk,
        边界='相邻已完成观测点的离线速率，不是生产双点建议；重复快照不是独立机会。基础资格仅检查满区、预算和SST覆盖，不含实际GC时刻、动态门槛、目标资源、稳定复核或相对Greedy额外预算。零观察不等于连续时间不存在。')

def maintenance_audit(lines):
    pending=None;rr=[];zc_observations={};candidates=[];round_id=0
    for number,line in enumerate(lines,1):
        if 'GC 检查：' in line:zc_observations={}
        if 'Zone 维护观测：' in line:
            r=fields(line,'Zone 维护观测：')
            if r['阶段']=='RR开始':pending=r
            if r['阶段']=='ZC候选':zc_observations[r['Zone']]=r
        if 'V1影子候选：' in line:
            c=fields(line,'V1影子候选：');round_id=int(c['轮次'])
            if c.get('原候选资格')=='1':
                o=zc_observations.get(c['Zone']);require(o is not None,'候选缺主机代际观测')
                generations=o['主机代际'].split('/')
                known=o['重置窗稳定']=='1' and len(set(generations))==1
                candidates.append(dict(轮次=round_id,Zone=c['Zone'],主机代际=generations[0] if known else None,行号=number))
        if (m:=RR_START.search(line)):
            zone,blocks,valid,tail=map(int,m.groups())
            require(pending and pending['Zone']==str(zone) and int(pending['有效字节'])==valid,'RR开始缺对应资格观测')
            generations=pending['主机代际'].split('/')
            require(pending['重置窗稳定']=='1' and len(set(generations))==1,'RR开始代际不稳定')
            rr.append(dict(行号=number,Zone=str(zone),主机代际=generations[0],
                达阈值块数=blocks,有效字节=valid,封闭尾部字节=tail,
                启动时回收预算满足=0<valid<=CAPACITY-RECLAIM,启动时满区=pending['满区']=='1'))
            pending=None
    require(pending is None,'RR开始观测未闭合')
    reused=[]
    for c in candidates:
        for r in rr:
            if c['Zone']==r['Zone'] and c['行号']<r['行号']:
                reused.append(dict(候选=c,后续RR=r,同一主机代际=c['主机代际'] is not None and c['主机代际']==r['主机代际'],
                    说明='只比较各自的主机代际，禁止把主机代际数值与设备代际直接比较'))
    return dict(RR启动数=len(rr),启动时预算满足数=sum(r['启动时回收预算满足'] for r in rr),
        RR启动事实=rr,同地址后续RR核对=reused,
        边界='RR启动时状态不能替代更早候选状态。同地址跨Reset重用不等于原候选后来触发RR，也不能据此声称可避免RR。')

def audit(root):
    require(root.name==LABEL,'只核对已固定的双点短测')
    saved=json.loads((root/'独立核验.json').read_text())
    require(pair_audit(root)==saved,'原独立核验复算不一致')
    run=root/'运行归档'/LABEL
    reads=rows(run/'reads.csv')
    counts=Counter('固定不覆盖键' if r['键号']=='524287' else '覆盖写前缀热点' for r in reads)
    fixed=[r for r in reads if r['键号']=='524287']
    require(all(r['版本']=='0' for r in fixed),'固定不覆盖键出现更新版本')
    result=dict(原独立核验逐字段一致=True,实际选区=saved['双点速率核验'],
        全阶段观测=snapshot_audit(rows(run/'zones.csv'),rows(run/'files.csv')),
        维护事实=maintenance_audit((run/'zenfs.run.log').read_text().splitlines()),
        逻辑热点事实=dict(读请求分组=dict(counts),固定不覆盖键=524287,固定键版本集合=sorted({r['版本'] for r in fixed}),
            覆盖写键域='0至262143，循环覆盖',另一半读键域='0、1024、…、15360',
            物理映射边界='日志没有逐Get的SST／PPA跟踪，不能据逻辑键域直接断言具体热块属于哪种读请求'),
        输入身份={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('zenfs.run.log','zones.csv','files.csv','reads.csv','writes.csv')},
        分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        未改变='生产代码、选择策略、RR阈值、GC条件、候选资格、原始日志；未启动新设备实验')
    return result

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要双点实验归档根目录')
    root=Path(sys.argv[1]).resolve();output=root/'机会覆盖诊断.json'
    require(not output.exists(),'输出已存在，拒绝覆盖')
    result=audit(root);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('机会覆盖离线核验完成，未运行新设备实验。')
