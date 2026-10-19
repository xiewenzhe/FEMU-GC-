"""事后标注读／失效顺序与逐文件收益；这些标签不输入选区器。"""
import re
from statistics import mean
from analyze_cost_rank import analyze as base_analyze,compare as base_compare
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require
from analyze_v1_victim_value import stamp
from lifetime_plan import LABEL,early_events,name,MIB

def annotate(run,result,retire_delay=0):
    ops=rows(run/'operations.csv');lines=(run/'zenfs.run.log').read_text().splitlines()
    origin=[stamp(line)-int(m[1])/1e9 for line in lines if (m:=re.search(r'滚动更新提交：代号=\d+，经过纳秒=(\d+)',line))]
    require(origin and max(origin)-min(origin)<.020,'时钟未对齐');start=mean(origin)
    created={r['文件']:int(r['开始纳秒'])/1e9 for r in ops if r['操作']=='创建' and '.anchor.sst' in r['文件']}
    deleted={r['文件'].removesuffix('.retired.bin'):(int(r['开始纳秒'])+int(r['延迟纳秒']))/1e9
             for r in ops if r['操作']=='删除' and '.anchor.sst' in r['文件']}
    released={}
    for line in lines:
        if '寿命负载读句柄释放：' in line:
            r=fields(line,'寿命负载读句柄释放：');released[r['文件']]=int(r['经过纳秒'])/1e9
    retirements=[]
    for due,g,k in early_events(retire_delay):
        n=name(g,k);require(n in deleted and n in released and released[n]<deleted[n],'句柄未先释放或没有退役')
        require(deleted[n]>=due,'退役早于固定计划')
        retirements.append(dict(文件=n,代际=g,计划删除秒=due,实际删除秒=deleted[n],读句柄释放秒=released[n],
                                计划迟到秒=deleted[n]-due,实际复制=result['逐文件'].get(n,dict(迁移MiB=0,次数=0,维护类型={}))))
    # 候选的事前特征和未来删除标签分开保存；不由标签重新计算选择。
    files=[];candidates={};costs={};features=[]
    for line in lines:
        if '简单双因素候选：' in line:
            c=fields(line,'简单双因素候选：');candidates[c['Zone']]=c
        if '独立约束候选：' in line:
            c=fields(line,'独立约束候选：');costs[c['Zone']]=c
        if 'V1影子文件：' in line:files.append(fields(line,'V1影子文件：'))
        if '独立约束决策：' in line:
            d=fields(line,'独立约束决策：');at=stamp(line)-start
            for f in files:
                n=f['文件名'];z=f['Zone'];c=candidates[z];s=costs[z]
                features.append(dict(轮次=d['轮次'],决策经过秒=at,文件=n,文件编号=f['文件编号'],
                    事前可见=dict(文件年龄秒=at-created[n] if n in created else None,区有效MiB=int(c['有效字节'])/MIB,
                        区最大块RC=int(c['最大块读数']),ETA秒=int(s['短期达阈值纳秒'])/1e9 if s['短期预测可知']=='1' else None),
                    事后标签=dict(后续删除秒=deleted.get(n),距删除秒=deleted[n]-at if n in deleted else None)))
            files=[];candidates={};costs={}
    require(not files and not candidates and not costs,'候选观察未闭合')
    result.update(预设早退役=retirements,候选事前特征与事后标签=features,
        说明='所有策略只使用原可观测量；寿命安排仅属于负载。本轮未增加失效预测，未采集的历史有效量变化不补造。')
    return result

def analyze(run,mode,expected,trial=None):
    return annotate(run,base_analyze(run,mode,expected,lifetime=True,lifetime_trial=trial),20 if trial=='delay20' else 0)

def compare(arms):
    result=base_compare(arms);baseline=arms['greedy'];per_generation=[]
    for g in range(4):
        totals={m:sum(v['迁移MiB'] for n,v in r['逐文件'].items() if n.startswith(f'/rrzc-stream/g{g}.')) for m,r in arms.items()}
        per_generation.append(dict(代际=g,预设类别=('冷区早退役','正常寿命','热区早退役')[g%3],
            实际迁移MiB=totals,相对Greedy={m:v-totals['greedy'] for m,v in totals.items()}))
    result.update(逐代净迁移=per_generation,边界='混合失效时序的180秒受控配对；各代存在空间和目标区交互，分类差值不等于独立场景的因果效应。')
    return result
