"""将短窗与后续同量写入分账，检验RR联合收益是否只是延后的冷区GC。"""
import json,re
from pathlib import Path
from analyze_joint_time import analyze_arm as original_arm,compare as original_compare
from analyze_joint_opportunity import fields,require,table
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS

def ledger(lines):
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    copies=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr)
    require(all(r['复制成功']=='1' for r in copies) and sum(int(r['有效字节']) for r in copies)==moved,
            '分阶段逐文件复制账目不闭合')
    resets=[tuple(map(int,m.groups())) for s in lines if
        (m:=re.search(r'Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，',s))]
    require(all(c==b+1 for _,b,c in resets),'分阶段Reset代际错误')
    return dict(ZC次数=sum(r[2] for r in zc),RR次数=sum(r[2] for r in rr),
        ZC复制字节=sum(r[1] for r in zc),RR复制字节=sum(r[1] for r in rr),总复制字节=moved,
        总成功ZoneReset=len(resets),推导Block擦除=len(resets)*128)

def unique(lines,tag):
    indexes=[i for i,s in enumerate(lines) if tag in s]
    require(len(indexes)==1,'后续阶段边界缺失或重复：'+tag)
    return indexes[0]

def analyze_arm(root,mode,*,terminal=False,terminal_version=1):
    root=Path(root);result=original_arm(root,mode,'opportunity',terminal=terminal,terminal_version=terminal_version)
    lines=(root/'zenfs.run.log').read_text().splitlines()
    release=unique(lines,'联合维护释放：');initial=unique(lines,'联合维护观察结束：')
    begin=unique(lines,'后续空间压力开始：');written=unique(lines,'后续空间写入完成：')
    end=unique(lines,'后续空间压力结束：')
    require(initial<begin<written<end,'后续阶段时间顺序错误')
    plan=fields(lines[begin],'后续空间压力开始：');done=fields(lines[end],'后续空间压力结束：')
    require(plan==dict(文件='/v1-execution-smoke/followup.bin',写入字节=str(128<<20),载荷种子='180',
            写后等待纳秒='25000000000'),'实际后续输入与登记不同')
    require(int(done['实际写后纳秒'])>=25*10**9 and done['计划写入字节']==str(128<<20) and
            done['计划写后等待纳秒']=='25000000000','后续观察不完整')
    require('后续文件完整载荷校验通过：128MiB，种子180' in (root/'verify.stdout.log').read_text(),
            '新增载荷没有完成全量核验')
    points=table(root/'followup.space.csv')
    require([r['阶段'] for r in points]==['写入前','写入后','等待后'],'空间检查点不完整')
    zones=table(root/'followup.zones.csv')
    require(all(sum(z['阶段']==p for z in zones)>0 for p in ('写入前','写入后','等待后')),'缺失主机Zone快照')
    phases={'准备':ledger(lines[:release+1]),'前25秒':result['窗口内'],'窗口后至追加前':ledger(lines[initial+1:begin]),
            '追加写入':ledger(lines[begin+1:written]),'写后25秒':ledger(lines[written+1:end]),
            '后续结束至业务返回':ledger(lines[end+1:])}
    require(all(sum(v[k] for v in phases.values())==result['全业务'][k] for k in result['全业务']),
            '后续与初窗分阶段账目未闭合')
    hardware=json.loads((root/'zones.final.json').read_text())['zone_list']
    writable=sum(max(0,z['slba']+z['cap']-z['wp'])*512 for z in hardware if z['state']!='FULL')
    result.update(分阶段=phases,后续输入=plan,主机空间检查点=points,
        结束时硬件可写字节=writable,
        边界='真实初窗25秒，再同量128MiB写入及写后25秒；非YCSB性能实验；主机空间与硬件可写空间不等同')
    return result

def compare(arms):
    result=original_compare(arms);a,b=arms['space'],arms['joint']
    require(a['后续输入']==b['后续输入'],'两组后续输入不相同')
    result.update(分阶段差值={p:{k:b['分阶段'][p][k]-a['分阶段'][p][k]
        for k in a['分阶段'][p]} for p in a['分阶段']},
        结束时硬件可写字节差=b['结束时硬件可写字节']-a['结束时硬件可写字节'],
        边界='同量后续写入检验空间债务；不要求收益为正，不据此推断所有负载或稳态表现')
    return result
