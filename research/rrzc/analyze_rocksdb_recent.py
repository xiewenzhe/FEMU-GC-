"""核验独立采样和候选时刻的同块证据；不把观察解释为机制收益。"""
import json
from collections import Counter
from analyze_rocksdb_observe import analyze,factor_summary
from analyze_rocksdb_pressure import gc_audit
from analyze_v1_shadow_stream import fields,require
from analyze_v1_growth_stream import qualifies

LABEL='rocksdb-recent-20261016'

def sampling_audit(lines):
    samples=[];windows=[];ongoing=None;frozen=[];gates={};rounds=[]
    for number,line in enumerate(lines,1):
        if '独立近期采样：' in line:
            row=fields(line,'独立近期采样：');row['行号']=number;samples.append(row)
            if ongoing is not None:ongoing['内部采样数']+=1
        if 'RR 开始：' in line:
            require(ongoing is None,'RR窗口未闭合');ongoing=dict(开始行=number,内部采样数=0)
        if 'RR 统计：' in line:
            require(ongoing is not None,'RR统计无开始');ongoing['结束行']=number
            windows.append(ongoing);ongoing=None
        if '独立近期采样冻结：' in line:frozen.append(fields(line,'独立近期采样冻结：'))
        if 'V1增长候选：' in line:
            row=fields(line,'V1增长候选：');gates[row['轮次'],row['Zone']]=row
    require(ongoing is None and samples,'采样或RR记录不完整')
    coverage=gc_audit(lines)
    require(len(frozen)==len(coverage['进入选区详情']),'冻结记录与选区轮次不符')
    require([int(f['发布序号']) for f in frozen]==sorted(int(f['发布序号']) for f in frozen),'发布序号倒退')
    for r in coverage['进入选区详情']:
        d=r['决策'];rid=d['轮次'];eligible=[]
        for c in r['单区原始候选']:
            if qualifies(c):
                g=gates[rid,c['Zone']]
                if g['增量可知']=='1':
                    require(500000000<=int(g['间隔纳秒'])<=2000000000,'可知增量超出原时间界限')
                eligible.append(dict(Zone=c['Zone'],是Greedy=c['Zone']==d['GreedyZone'],
                    增量可知=g['增量可知']=='1',间隔纳秒=int(g['间隔纳秒']),
                    真实增量=int(g['真实增量']),原因=g['原因']))
        rounds.append(dict(轮次=rid,合格候选=eligible))
    all_candidates=[c for r in rounds for c in r['合格候选']]
    greedy=[c for c in all_candidates if c['是Greedy']]
    return dict(采样总数=len(samples),采样失败数=sum(s['可用']!='1' for s in samples),
        RR期间采样=windows,选区同块证据=rounds,
        合格候选观察数=len(all_candidates),合格候选增量可知数=sum(c['增量可知'] for c in all_candidates),
        Greedy观察数=len(greedy),Greedy增量可知数=sum(c['增量可知'] for c in greedy),
        未知原因=dict(Counter(c['原因'] for c in all_candidates if not c['增量可知'])),
        说明='同一块的累计RC差；未知不视为零。最新采样过近仍回退，不回找较旧点。单次不同负载观察不证明性能收益。')

def audit(root):
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'运行期间生产身份改变')
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    run=root/'运行归档'/LABEL
    result=analyze(run,expected,recent=True)
    lines=(run/'zenfs.run.log').read_text().splitlines()
    require(any('独立近期采样配置：启用=1' in s for s in lines),'独立采样未启用')
    result['独立采样核验']=sampling_audit(lines)
    result['原GC候选覆盖']=gc_audit(lines)
    result['事前信号汇总']=factor_summary(result)
    return result

if __name__=='__main__':
    import sys
    from pathlib import Path
    require(len(sys.argv)==2,'需要归档目录');root=Path(sys.argv[1]).resolve()
    require(root.name==LABEL and not (root/'独立核验.json').exists(),'目录不符或已核验')
    (root/'独立核验.json').write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
