"""同库同固定请求的Greedy／已有可执行V1配对；不混用双点影子或新成本预测。"""
import hashlib,json
from pathlib import Path
from analyze_rocksdb_observe import analyze
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_START,RR_STATS
from analyze_stream import rows
from analyze_v1_shadow_stream import require
from guarded_rounds import parse
from analyze_v1_shadow_stream import fields

LABEL='rocksdb-guard-pair-20261016'
GUEST_LABEL='rocksdb-anchor-slow-20261016'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT=f'/home/femu/rrzc-stream-results/{GUEST_LABEL}'
MIB=1<<20

def request_identity(reads,writes):
    """相对计划时间和逻辑请求必须相同；不要求并发读出的版本或调度顺序相同。"""
    require(reads and writes,'缺配对请求')
    origin=int(reads[0]['计划纳秒']);digests={}
    for label,requests,keys in (('读',reads,('序号','键号')),('写',writes,('序号','起始键序号','键数','起始版本'))):
        h=hashlib.sha256()
        for r in requests:
            row=[r[k] for k in keys]+[int(r['计划纳秒'])-origin]
            h.update((json.dumps(row,ensure_ascii=False)+'\n').encode())
        digests[label]=h.hexdigest()
    return digests

def terminal_space(zones):
    require(zones,'末次IO区快照缺失');batch=zones[-1]['批次']
    last=[r for r in zones if r['批次']==batch]
    require(len(last)==29 and len({r['Zone'] for r in last})==29,'末次IO区快照不完整或重复')
    require(all(r['阶段']=='2' and int(r['容量字节'])==256*MIB for r in last),'末次阶段或几何不符')
    return dict(批次=batch,IO区数=len(last),重置观察稳定区数=sum(r['统计稳定']=='1' for r in last),
        IO区可写剩余MiB=sum(int(r['剩余字节']) for r in last)/MIB,
        IO区有效MiB=sum(int(r['有效字节']) for r in last)/MIB,
        IO区零有效区数=sum(int(r['有效字节'])==0 for r in last),
        边界='业务末次29个IO区的非原子快照，不含元数据区；剩余合计不等于寿命匹配、令牌和预留约束下可分配空间，不能仅据此判断分配安全。')

def analyze_arm(root,mode,expected):
    require(mode in ('greedy','guarded') and root.name==mode,'配对组目录不符')
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'本组软件身份变化')
    run=root/'运行归档'/GUEST_LABEL
    result=analyze(run,expected,execution_pair=mode)
    require(result['维护无错误'] and result['RR尝试']==result['RR成功'],'维护有错误或RR未完整重置')
    lines=(run/'zenfs.run.log').read_text().splitlines()
    if any('独立约束决策：' in s for s in lines):
        decisions,_,maintenance=parse(lines,'guarded' if mode=='guarded' else 'greedy',cost_rank=False)
    else:decisions=[];maintenance=[]
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    require(all(row[2]==1 for row in zc+rr),'出现非单区完整维护')
    reads,writes=rows(run/'reads.csv'),rows(run/'writes.csv')
    result.update(配对模式=mode,实际改选次数=sum(d['独立核验改选'] for d in decisions),
        原建议次数=sum(d['原会改选']=='1' for d in decisions),
        建议通过全部复核次数=sum(d['复核后可改选']=='1' for d in decisions),
        决策=decisions,维护结果=maintenance,ZC迁移MiB=sum(r[1] for r in zc)/MIB,
        RR迁移MiB=sum(r[1] for r in rr)/MIB,请求身份=request_identity(reads,writes),
        终态IO快照=terminal_space(rows(run/'zones.csv')),
        结论边界='同固定负载单次配对，使用现有短期紧迫度／预算／尾部V1；不是双点估速真实执行或新成本预测模型，不代表稳态、统计显著性或普遍收益。')
    return result

def compare(arms):
    require(set(arms)=={'greedy','guarded'},'缺配对组')
    baseline=arms['greedy'];changed=arms['guarded']
    require(baseline['软件身份']==changed['软件身份'] and baseline['请求身份']==changed['请求身份'],'两组源码、库、二进制或固定请求不同')
    require(baseline['实际改选次数']==0,'基线发生真实改选')
    delta={k:changed[k]-baseline[k] for k in ('ZC','RR成功','总迁移MiB','ZC迁移MiB','RR迁移MiB')}
    return dict(配对核验通过=True,唯一策略开关='ZENFS_RRZC_GUARDED_MODE：shadow／execute',
        实际改选次数=changed['实际改选次数'],相对Greedy=delta,
        请求封装耗时={mode:a['平均与分位请求封装耗时'] for mode,a in arms.items()},
        终态IO快照={mode:a['终态IO快照'] for mode,a in arms.items()},
        结论='本次发生真实改选，报告净迁移和维护差，不只报告RR下降。' if changed['实际改选次数'] else '本次未发生真实改选，不能检验改选收益，组间数值差不归因于选择策略。',
        边界='单次串行配对，主机环境与并发压实有波动；请求封装耗时不是纯API、设备或落盘延迟。重复迁移仍未知，不用同名SST复制次数替代逻辑片段重复量。')

def audit(root):
    require(root.name==LABEL,'不是本轮配对根目录')
    expected=json.loads((root/'同库身份.json').read_text())
    arms={mode:analyze_arm(root/mode,mode,expected) for mode in ('greedy','guarded')}
    return dict(汇总=compare(arms),各组=arms)

def completed_rr_prefix(lines,cutoff):
    """只统计给定日志行之前已完成的RR；不跨客机按Zone地址认定同一份数据。"""
    events=[];pending=None
    for number,line in enumerate(lines,1):
        if number>=cutoff:break
        if (m:=RR_START.search(line)):
            require(pending is None,'前缀RR重复开始')
            pending=dict(开始行号=number,开始时钟=line[:26],Zone=m.group(1),源有效字节=int(m.group(3)))
        if (m:=RR_STATS.search(line)):
            require(pending is not None,'前缀RR统计缺开始')
            events.append(dict(pending,完成行号=number,实际迁移字节=int(m.group(2))))
            pending=None
    require(pending is None,'截止点落在未完成RR中，不能生成完整前缀账目')
    return events

def timing_audit(root):
    """后验时序复核独立保存，原配对核验和冻结源保持不变。"""
    original=audit(root)
    require(original==json.loads((root/'配对核验.json').read_text()),'原配对复算不一致')
    all_lines={mode:(root/mode/'运行归档'/GUEST_LABEL/'zenfs.run.log').read_text().splitlines() for mode in ('greedy','guarded')}
    before_zc={}
    for mode,lines in all_lines.items():
        cutoff=next((n for n,s in enumerate(lines,1) if '独立约束决策：' in s),len(lines)+1)
        before_zc[mode]=completed_rr_prefix(lines,cutoff)
    switches=[d for d in original['各组']['guarded']['决策'] if d['独立核验改选']]
    details=[]
    for d in switches:
        prefix=completed_rr_prefix(all_lines['guarded'],d['行号'])
        blocks={}
        for line in all_lines['guarded'][:d['行号']]:
            if 'V1影子候选：' in line:
                b=fields(line,'V1影子候选：')
                if b['轮次']==d['轮次']:blocks[b['Zone']]=b
        details.append(dict(轮次=d['轮次'],实际Zone=d['实际Zone'],改选前已完成RR数=len(prefix),
            改选前已完成RR迁移MiB=sum(r['实际迁移字节'] for r in prefix)/MIB,
            本次额外源有效搬运MiB=int(d['实际额外有效字节'])/MIB,
            Greedy当前点=blocks[d['GreedyZone']],改选当前点=blocks[d['实际Zone']]))
    signature=lambda r:(r['Zone'],r['源有效字节'],r['实际迁移字节'])
    a,b=before_zc['greedy'],before_zc['guarded']
    first_difference=next((i for i,(x,y) in enumerate(zip(a,b),1) if signature(x)!=signature(y)),None)
    return dict(配对完整复算一致=True,各组首轮ZC决策前RR=before_zc,
        首轮ZC前共同覆盖RR序号中首个不同位置=first_difference,真实改选时点=details,
        整轮差额=original['汇总']['相对Greedy'],
        输入配对核验SHA256=hashlib.sha256((root/'配对核验.json').read_bytes()).hexdigest(),
        后验分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        边界='相同逻辑请求和软件不保证并发压实及物理布局相同。改选前已有差异时，整轮差额不能全归因于该次改选；本复核不声称已确认差异的全部根因，也不把跨组同地址当同代数据。')

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要配对归档根目录');root=Path(sys.argv[1]).resolve()
    output=root/'配对核验.json';require(not output.exists(),'结果已存在，拒绝覆盖')
    output.write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
    print('同库同请求及真实选择／尾部复核／完整维护账目核验通过。')
