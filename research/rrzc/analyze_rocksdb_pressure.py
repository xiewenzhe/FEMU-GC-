"""重算原GC动态门槛与真实候选覆盖，不改变原候选资格。"""
import hashlib,json,re
from collections import Counter
from pathlib import Path
from analyze_rocksdb_observe import analyze as base_analyze,factor_summary
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require
from analyze_v1_growth_stream import qualifies
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_periodic import latency
from guarded_rounds import parse

LABEL='rocksdb-pressure-20261016'
GC=re.compile(r'GC 检查：空闲=(\d+) 字节，非空闲=(\d+) 字节，空闲比例=(\d+)%，启动水平=(\d+)%，进入选区=(\d+)')
CANDIDATE=re.compile(r'ZC 候选：Zone (\d+)，无效比例 (\d+)%.*有效数据 (\d+) 字节.*选中 (\d+)')
SUMMARY=re.compile(r'GC 选区汇总：检查空闲比例=(\d+)%.*无效比例门槛=(\d+)%')

def space_selected(valid,capacity,free_percent):
    require(0<=free_percent<=20 and 0<=valid<=capacity and capacity>0,'选区几何或空闲比例非法')
    garbage=100-100*valid//capacity;threshold=100-3*(20-free_percent)
    return garbage>threshold and garbage<100

def gc_audit(lines):
    checks=[];rounds=[];current=None
    for number,line in enumerate(lines,1):
        if (m:=GC.search(line)):
            require(current is None,'前一GC轮次未闭合')
            free,used,pct,start,entered=map(int,m.groups())
            require(start==20 and pct==100*free//(free+used) and entered==int(pct<=20),'GC原条件重算不符')
            checks.append(dict(行号=number,空闲字节=free,非空闲字节=used,空闲比例=pct,进入选区=bool(entered)))
            if entered:current=dict(检查=checks[-1],动态无效比例门槛=100-3*(20-pct),满区观察=[],原空间通过=[],单区原始候选=[],合格单区候选=[],决策=None)
        if (m:=CANDIDATE.search(line)):
            require(current is not None,'候选没有GC检查')
            zone,garbage,valid,selected=map(int,m.groups());pct=current['检查']['空闲比例']
            require(garbage==100-100*valid//(256<<20) and selected==int(space_selected(valid,256<<20,pct)),'原空间资格重算不符')
            r=dict(Zone=zone,有效字节=valid,无效比例=garbage,原空间通过=bool(selected));current['满区观察'].append(r)
            if selected:current['原空间通过'].append(zone)
        if '简单双因素候选：' in line:
            require(current is not None,'单区候选没有原GC检查')
            c=fields(line,'简单双因素候选：')
            require(int(c['Zone']) in current['原空间通过'],'单区候选未通过原空间门槛')
            current['单区原始候选'].append(c)
            if qualifies(c):current['合格单区候选'].append(c)
        if '独立约束决策：' in line:
            require(current is not None and current['决策'] is None,'选区决策重复或缺GC')
            current['决策']=fields(line,'独立约束决策：')
        if (m:=SUMMARY.search(line)):
            require(current is not None and current['决策'] is not None,'GC汇总缺完整决策')
            pct,threshold=map(int,m.groups())
            require(pct==current['检查']['空闲比例'] and threshold==current['动态无效比例门槛'],'原动态门槛不符')
            rounds.append(current);current=None
    require(current is None,'最后GC轮次缺汇总')
    return dict(检查数=len(checks),进入选区数=len(rounds),至少一个合格单区的轮数=sum(bool(r['合格单区候选']) for r in rounds),
        至少两个合格单区的轮数=sum(len(r['合格单区候选'])>=2 for r in rounds),
        全部检查=checks,进入选区详情=rounds,
        说明='20%只开始检查，恰好20%时门槛100%，严格大于门槛且小于100%的条件无解；未修改该原始行为')

def request_comparison(previous,run):
    for name,keys,count in (('reads.csv',('序号','键号'),48000),('writes.csv',('序号','起始键序号','键数','起始版本'),15360)):
        old,new=rows(previous/name),rows(run/name)
        require(len(old)==len(new)==count and [[r[k] for k in keys] for r in old]==[[r[k] for k in keys] for r in new],
                '相对旧场景混合请求改变或未完成')

def audit(root):
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'生产身份前后改变')
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    run=root/'运行归档'/LABEL
    result=base_analyze(run,expected,pressure=True)
    result['原GC门槛与候选覆盖']=gc_audit((run/'zenfs.run.log').read_text().splitlines())
    previous=root.parent/'rocksdb-observe-20261016/运行归档/rocksdb-observe-20261016'
    result['旧场景无候选原因核验']=gc_audit((previous/'zenfs.run.log').read_text().splitlines())
    # 比较完整请求内容，不要求两个不同运行的实际排程时刻相同。
    request_comparison(previous,run)
    result['混合请求与旧场景逐项一致']=True
    result['事前信号汇总']=factor_summary(result)
    return result

def diagnose(root):
    """仅解释已失败且保留的观察；不放宽成功分析器的退出／事件检查。"""
    run=root/'运行归档'/LABEL;previous=root.parent/'rocksdb-observe-20261016/运行归档/rocksdb-observe-20261016'
    require((run/'run.exit').read_text().strip()=='1' and not (run/'verify.exit').exists(),'不是原业务失败且未完成正常校验的归档')
    for line in (run/'logs.sha256').read_text().splitlines():
        digest,name=line.split(maxsplit=1);relative=Path(name).relative_to('/home/femu/rrzc-stream-results/'+LABEL)
        require(hashlib.sha256((run/relative).read_bytes()).hexdigest()==digest,'原始失败日志哈希不符')
    require((run/'identity.initial.sha256').read_bytes()==(run/'identity.final.sha256').read_bytes() and
            (root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'失败期间软件身份改变')
    actual={n:d for d,n in (s.split() for s in (run/'identity.initial.sha256').read_text().splitlines())}
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    require(all(actual.get(n)==d for n,d in expected.items()),'实际生产库／测试身份不符')
    request_comparison(previous,run)
    lines=(run/'zenfs.run.log').read_text().splitlines();decisions,_,maintenance=parse(lines,'greedy',cost_rank=False)
    coverage=gc_audit(lines)
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    require(len(zc)==len(maintenance) and all(r[1]==int(m['本轮迁移字节']) for r,m in zip(zc,maintenance)),'ZC账目不符')
    require(sum(r[2] for r in rr)==sum('RR 完成：' in s for s in lines),'RR成功重置账目不符')
    copies=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr);require(sum(int(c['有效字节']) for c in copies)==moved,'失败尝试在内的实际迁移总账不符')
    events=rows(run/'events.csv');failed=Counter(e['事件'] for e in events if e['成功']=='0')
    growth={};freshness=[]
    for s in lines:
        if 'V1增长候选：' in s:
            g=fields(s,'V1增长候选：');growth[g['轮次'],g['Zone']]=g
        if '独立约束决策：' in s:
            d=fields(s,'独立约束决策：')
            if d['存在Greedy']=='1':
                g=growth[d['轮次'],d['GreedyZone']]
                freshness.append(dict(轮次=d['轮次'],GreedyZone=d['GreedyZone'],原因=g['原因'],前点间隔秒=int(g['间隔纳秒'])/10**9))
    diagnostic=root/'只读诊断归档';status=(diagnostic/'diagnostic.exit').read_text().strip()
    require(status=='0' and '最新值全量校验通过：键数=655360，业务字节=2684354560' in (diagnostic/'diagnostic-verify.stdout.log').read_text(),'失败后只读最新值诊断未通过')
    require(not any(marker in (diagnostic/'zenfs.verify.log').read_text() for marker in
                    ('RR 开始：','GC 检查：','[ERROR]')),'只读诊断出现维护或错误')
    database=root/'补充数据库日志'/f'{LABEL}-aux/rrzc-real/LOG'
    errors=[s for s in database.read_text().splitlines() if 'Zone allocation failure' in s and 'EVENT_LOG' not in s]
    require(errors and 'Operation timed out' in (run/'run.stdout.log').read_text(),'原始失败原因证据不足')
    return dict(整轮成功=False,正式基线可用=False,原业务退出码=1,只读诊断最新值通过=True,
        前台读请求数=48000,前台覆盖写键数=983040,混合请求与旧场景逐项一致=True,
        生产与测试身份核验通过=True,原始归档哈希核验通过=True,原GC候选覆盖=coverage,
        ZC成功=len(zc),RR尝试=len(rr),RR成功=sum(r[2] for r in rr),总实际迁移MiB=moved/(1<<20),
        RR未重置尝试实际迁移MiB=sum(r[1] for r in rr if r[2]==0)/(1<<20),
        失败回调=dict(failed),Greedy同块近期统计=freshness,
        数据库分配错误旁证=errors[:4],主程序失败原文=(run/'run.stdout.log').read_text().strip(),
        请求封装耗时=dict(读Get封装=latency([int(r['延迟纳秒']) for r in rows(run/'reads.csv')]),
            写64键批次封装=latency([int(r['延迟纳秒']) for r in rows(run/'writes.csv')])),
        计时边界='沿用旧观察的请求封装计时，含请求构造，非纯API、落盘或设备延迟；失败运行不做性能比较',
        说明='只读诊断通过不能把原业务收尾压实失败改写为成功；不补原verify.exit，不重跑，不改变机制，客机现场保留')

if __name__=='__main__':
    import sys
    require(len(sys.argv) in (2,3) and (len(sys.argv)==2 or sys.argv[2]=='--diagnose'),'需要本轮压力观察根目录，可选--diagnose')
    root=Path(sys.argv[1]).resolve();require(root.name==LABEL,'归档路径不符')
    diagnostic=len(sys.argv)==3
    output=root/('失败诊断.json' if diagnostic else '独立核验.json');require(not output.exists(),'结果已存在，不覆盖')
    result=diagnose(root) if diagnostic else audit(root);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('真实请求、数据和原GC候选门槛核验完成；是否形成比较机会见覆盖结果。')
