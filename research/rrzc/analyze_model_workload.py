#!/usr/bin/env python3
"""独立成本负载核验；仅判断候选覆盖，不将影子建议当真实收益。"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

from analyze_stream import rows, expected_reads
from analyze_v1_shadow_stream import fields, require
from analyze_v1_growth_stream import qualifies
from analyze_model_scope import decision_features
from analyze_tail_ablation_fixed import expected_software
from guarded_rounds import parse
from analyze_tail_stream import ledger
from analyze_guarded_stream import window_account
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS

MIB=1<<20


def planned_files(g):
    result={}
    for kind,size in (('cold',16),('hot',(16,24,32)[g%3])):
        result[f'/rrzc-stream/g{g}.{kind}.anchor.sst']=size
        result[f'/rrzc-stream/g{g}.{kind}.version0.bin']=256-size
    return result


def check_trace(operations):
    serials=[0,0];reads=[];writes=defaultdict(list)
    for r in operations:
        t=int(r['线程']);require(t in (0,1),'未知线程')
        require(int(r['序号'])==serials[t] and r['成功']=='1','序号或请求失败')
        serials[t]+=1;g=int(r['代号'])
        if r['操作']=='读4KiB':
            require(t==1 and int(r['字节'])==4096,'读请求错误')
            kind=int('.hot.' in r['文件'])
            require(r['文件']==f"/rrzc-stream/g{g}.{'hot' if kind else 'cold'}.anchor.sst",'读文件错误')
            reads.append((g,kind,r['阶段'],int(r['偏移'])))
        elif r['操作']=='写1MiB' and g>=0:
            require(t==0 and int(r['字节'])==MIB,'写请求错误')
            writes[g].append((r['文件'],int(r['偏移'])))
    require(reads==[r[1:] for r in expected_reads(180)],'原读计划未完整执行')
    require(set(writes)==set(range(6)),'代际写入缺失')
    for g,actual in writes.items():
        planned={(n,i*MIB) for n,size in planned_files(g).items() for i in range(size)}
        require(len(actual)==512 and set(actual)==planned,'成本负载写入量／偏移不符')
    return dict(读请求数=len(reads),业务写入MiB=sum(map(len,writes.values())))


def coverage(lines):
    candidates={};costs={};sizes=Counter();features=[];rounds=[]
    for line in lines:
        if '简单双因素候选：' in line:
            c=fields(line,'简单双因素候选：');candidates[c['Zone']]=c
            if qualifies(c):sizes[int(c['有效字节'])//MIB]+=1
        if '独立约束候选：' in line:
            c=fields(line,'独立约束候选：');costs[c['Zone']]=c
        if '独立约束决策：' in line:
            d=fields(line,'独立约束决策：')
            require(set(candidates)==set(costs),'候选成本字段缺失')
            rounds.append(dict(轮次=d['轮次'],合资格候选有效MiB=sorted(int(c['有效字节'])/MIB for c in candidates.values() if qualifies(c))))
            if d['原会改选']=='1':features.append(decision_features(candidates,costs,d))
            candidates={};costs={}
    require(not candidates and not costs,'候选日志未闭合')
    combinations=Counter((f['Greedy有效MiB'],f['建议有效MiB'],f['预算内紧迫候选数']) for f in features)
    return dict(合资格候选有效MiB出现次数=dict(sizes),各轮候选=rounds,原建议数=len(features),建议特征=features,
        建议成本组合=[dict(Greedy有效MiB=g,建议有效MiB=p,预算内紧迫候选数=n,次数=count) for (g,p,n),count in sorted(combinations.items())],
        成本优先改变建议数=sum(f['成本优先排序改变建议'] for f in features))


def analyze(run):
    require(run.name=='model-cost-smoke-20261015-greedy','非本轮短测目录')
    for n in ('mkfs.exit','run.exit','verify.exit','runner.exit'):
        require((run/n).read_text().strip()=='0',n+'失败')
    require(rows(run/'config.csv')==[dict(用途='成本多样性功能验证',计划业务秒数='180',RR阈值='4096',
        GC启动比例='20',回收预算字节='201326592',额外预算字节='16777216',紧迫界限秒数='10',策略='greedy')],
        '固定功能配置不符')
    require((run.parent/'源码身份.before.sha256').read_bytes()==
            (run.parent/'源码身份.after.sha256').read_bytes(),'外部核心身份前后不符')
    guest=Path('/home/femu/rrzc-stream-results')/run.name
    for raw in (run/'logs.sha256').read_text().splitlines():
        digest,name=raw.split(maxsplit=1)
        require(hashlib.sha256((run/Path(name).relative_to(guest)).read_bytes()).hexdigest()==digest,'原始记录哈希错误')
    identity=(run/'identity.initial.sha256').read_bytes()
    require(identity==(run/'identity.final.sha256').read_bytes(),'运行期间身份改变')
    actual={n:d for d,n in (s.split() for s in identity.decode().splitlines())}
    require(all(actual.get(n)==d for n,d in expected_software().items()),'生产源／库改变')
    for n in ('model_workload_test.cc','model_workload_plan.h','run_model_workload_guest.sh'):
        require(actual[f'/home/femu/rrzc-{run.name}-tests/{n}']==hashlib.sha256((run.parent/n).read_bytes()).hexdigest(),'实际测试源不符')
    operations=rows(run/'operations.csv');requests=check_trace(operations)
    manifest=rows(run/'manifest.csv');expected={}
    for g in range(2,6):expected.update({n:v*MIB for n,v in planned_files(g).items() if '.anchor.' in n or g==5})
    expected.update({f'/rrzc-stream/filler{i}.bin':256*MIB for i in range(20)})
    require(len(manifest)==len(expected) and {r['文件']:int(r['有效字节']) for r in manifest}==expected,'最终清单不符')
    verify=(run/'verify.stdout.log').read_text()
    require(f'业务完整比对字节={632*MIB}' in verify and '填充文件首4KiB抽检=20' in verify,'完整数据校验失败')
    require(not any(s in (run/'zenfs.verify.log').read_text() for s in ('[ERROR]','RR 开始：','GC 检查：')),'只读校验异常')
    lines=(run/'zenfs.run.log').read_text().splitlines();text='\n'.join(lines)
    require('[ERROR]' not in text and '状态=失败' not in text,'维护错误')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',text))=={'20'},'GC水平改变')
    require([int(m[1]) for m in re.finditer(r'滚动更新提交：代号=(\d+)，',text)]==list(range(6)),'提交不完整')
    decisions,_,maintenance=parse(lines,'greedy',None)
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(len(zc)==len(maintenance) and text.count('RR 开始：')==text.count('RR 完成：')==len(rr),'维护未闭合')
    require(all(int(m['本轮迁移字节'])==r[1] for m,r in zip(maintenance,zc)),'ZC账目不符')
    require(int(rows(run/'states.csv')[-1]['经过纳秒'])>=180*10**9,'未到固定结束时点')
    events=ledger(lines)
    require(sum(int(c['有效字节']) for c in events['实际复制'])==sum(r[1] for r in zc+rr),'复制账目不符')
    return dict(核验通过=True,固定业务秒数=180,完整数据校验MiB=632,请求核验=requests,
        实际改选次数=sum(d['独立核验改选'] for d in decisions),ZC次数=len(zc),RR次数=len(rr),
        成本覆盖=coverage(lines),功能账目=window_account(lines,events['实际复制'],operations,0,180),
        终态空间=rows(run/'states.csv')[-1],结论边界='仅Greedy功能短测，不能据此评价稳态、收益或预测准确率。')


if __name__=='__main__':print(json.dumps(analyze(Path(sys.argv[1])),ensure_ascii=False,indent=2))
