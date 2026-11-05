#!/usr/bin/env python3
"""独立核验全块受控对照；保留结束空间差异，不把短窗维护减少当长期收益。"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
from gc_path_trace import fields

SECOND=10**9
def require(ok,why):
    if not ok:raise ValueError(why)
def eta(margin,span,delta):return (margin*span+delta-1)//delta if delta else 2**64-1
def analyze_arm(root):
    require((root/'wrapper.exit').read_text().strip()=='0','业务包装器未成功')
    raw=(root/'zenfs.run.log').read_text();log=raw.split('全块业务计量截止：',1)[0]
    require('全块业务计量截止：' in raw,'业务和完整内容核验没有划界')
    require('全块受控功能对照通过' in (root/'run.log').read_text(),'候选完整内容未通过')
    require('全块查询失败：' not in log,'全块查询出现错误，不能当作正常零风险')
    start=next(s for s in log.splitlines() if '全块对照起点：' in s)
    setup=fields(start,'全块对照起点：')
    earlier=log.split('全块对照起点：',1)[0]
    require('ZC 统计：' not in earlier and 'RR 统计：' not in earlier,'触发前已有维护，初始条件不再受控')
    migrated_zc=[int(v) for v in re.findall(r'ZC 统计：轮次 \d+，本轮迁移 (\d+) 字节',log)]
    migrated_rr=[int(v) for v in re.findall(r'RR 统计：尝试 \d+ 次，本次迁移 (\d+) 字节',log)]
    resets=len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功',log))
    maintenance_resets=sum(int(v) for v in re.findall(r'ZC 统计：[^\n]*?本轮重置 (\d+) 个 Zone',log))
    maintenance_resets+=sum(int(v) for v in re.findall(r'RR 统计：[^\n]*?本次重置 (\d+) 个 Zone',log))
    rounds=defaultdict(dict);representatives={};blocks=defaultdict(list);decisions=[]
    for line in log.splitlines():
        if '时序成本：' in line:
            row=fields(line,'时序成本：');rounds[int(row['轮次'])][int(row['Zone'])]=row
        elif '全块风险代表：' in line:
            row=fields(line,'全块风险代表：');representatives[int(row['轮次']),int(row['Zone'])]=row
        elif '全块风险明细：' in line:
            row=fields(line,'全块风险明细：');blocks[int(row['轮次']),int(row['Zone'])].append(row)
        elif '时序方案决策：' in line:
            row=fields(line,'时序方案决策：')
            if row['实际存在']=='1':decisions.append(row)
    details=[]
    for row in decisions:
        number=int(row['轮次']);selected=int(row['实际Zone'])
        safe=[r for r in rounds[number].values() if r['安全']=='1']
        require(safe,'没有安全候选')
        greedy=min(safe,key=lambda r:(int(r['有效']),int(r['Zone'])))
        require(selected in {int(r['Zone']) for r in safe},'选择了不安全候选')
        details.append(dict(轮次=number,实际Zone=selected,GreedyZone=int(greedy['Zone']),改选=selected!=int(greedy['Zone']),
            选中成本=rounds[number][selected],全块风险=representatives.get((number,selected))))
    audited=0
    for key,rows in blocks.items():
        representative=representatives[key]
        require(len(rows)==128 and sorted(int(r['块']) for r in rows)==list(range(128)),'全块分布不完整或编号重复')
        require(len({(r['快照'],r['代际'],r['会话'],r['代际起点']) for r in rows})==1,'不是同一Zone整帧')
        known=[r for r in rows if r['风险可知']=='1']
        for r in known:
            rc=int(r['RC']);delta=int(r['十秒增量']);predicted=2**64-1
            if rc>=1024:predicted=0
            elif delta:
                age=int(r['快照'])-int(r['代际起点']);span=min(age,10*SECOND)
                require(int(r['近期可知']) and 2*SECOND<=int(r['近期间隔'])<=4*SECOND,'同块短窗口无效')
                require(int(r['近期增量'])==rc-int(r['前RC']),'短增量不是同块真实读数差')
                predicted=max(eta(1024-rc,span,delta),eta(1024-rc,int(r['近期间隔']),int(r['近期增量'])))
            require(predicted==int(r['ETA']),'全块ETA不能从原始计数重算')
        if known:
            best=min(known,key=lambda r:(int(r['ETA']),int(r['块'])))
            require(int(best['块'])==int(representative['代表块']),'Zone代表不是最早预计到阈值块')
            require(int(best['RC'])==int(representative['代表RC']),'代表RC不匹配')
        audited+=len(rows)
    with (root/'输入与映射.csv').open() as f:requests=list(csv.DictReader(f))
    require(len(requests)==int(setup['后续请求']),'输入请求数不匹配')
    with (root/'结束空间.csv').open() as f:end=list(csv.DictReader(f))
    require(len(end)==1 and int(end[0]['历史最高RC'])<=1024,'结束空间或严格计数无效')
    with (root/'源区Reset核验.csv').open() as f:reset_rows=list(csv.DictReader(f))
    for r in reset_rows:
        if r['Empty']=='1':require(all(int(r[k])==0 for k in ('RC总和','十秒增量总和','三十秒增量总和')),'Reset后计数残留')
    return dict(通过=True,ZC次数=len(migrated_zc),RR次数=len(migrated_rr),ZC迁移字节=sum(migrated_zc),
        RR迁移字节=sum(migrated_rr),总迁移统计字节=sum(migrated_zc)+sum(migrated_rr),
        全部成功Reset含元数据=resets,维护统计Reset次数=maintenance_resets,
        改选次数=sum(r['改选'] for r in details),逐轮选择=details,
        全块重算条目=audited,初始状态=setup,结束空间=end[0],源区Reset=reset_rows,
        固定输入=[(r['请求'],r['逻辑偏移']) for r in requests])

def analyze(root):
    rows={};manifest={}
    for case in ('opportunity','stopped','low-yield'):
        paths=[root/(case+'-'+m) for m in ('greedy','current','blocks')]
        if not all(p.is_dir() for p in paths):continue
        arms={p.name:analyze_arm(p) for p in paths}
        base=arms[case+'-greedy']
        for value in arms.values():
            require(value['固定输入']==base['固定输入'],'同场景输入操作不同')
            for k in ('冷块RC','热块RC','后续请求','热偏移','空闲','非空闲'):
                require(value['初始状态'][k]==base['初始状态'][k],'初始空间、读计数或读取偏移不同：'+k)
        for key,value in arms.items():
            value.pop('固定输入')
            rows[key]=value
            for n in ('zenfs.run.log','run.log','结束空间.csv','输入与映射.csv','源区Reset核验.csv'):
                path=root/key/n;manifest[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    require(rows,'没有完整三策略配对')
    return rows,manifest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('guest_root',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    rows,manifest=analyze(a.guest_root)
    a.output.mkdir(exist_ok=True)
    for name,data in (('受控独立核验.json',rows),('分析来源.json',manifest)):
        with (a.output/name).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    lines=['# 全块风险受控功能实验','',
        '真实ZenFS受控文件I/O，4GiB/32Zone/128MiB每Zone、RR1024、GC20%。不是YCSB、稳态或吞吐量/延迟实验。',
        '每臂重置独立模拟盘并重建文件及读计数；相同场景固定输入一致，维护后物理读取和映射允许自然分化。',
        '', '| 场景与策略 | ZC | RR | 总迁移 MiB | 维护Reset | 改选 | 结束空闲 MiB |', '|---|---:|---:|---:|---:|---:|---:|']
    for key,r in rows.items():
        lines.append(f"| {key} | {r['ZC次数']} | {r['RR次数']} | {r['总迁移统计字节']/(1<<20):.3f} | {r['维护统计Reset次数']} | {r['改选次数']} | {int(r['结束空间']['结束空闲'])/(1<<20):.3f} |")
    lines+=['','总迁移采用ZenFS维护统计，不等于全部NAND编程字节。Reset为日志实际成功次数；不能把初始化全盘Reset算成业务擦除收益。',
        '各组结束空闲可能不同，必须同时报告；短窗RR或复制减少不能直接解释为等终态空间的长期净收益。',
        '本轮隔离全块风险输入，候选有效量稳定；未校准真实SST退役、目的区长期重读或泊松概率。',
        '全块ETA与代表块可从原始当前帧及已记录同块短窗口重算。历史身份连续性另由生产检查与换代际/PPA单元测试覆盖；日志没有逐帧导出所有历史PPA。','']
    with (a.output/'全块受控分析.md').open('x') as f:f.write('\n'.join(lines))
    print(json.dumps({k:{n:r[n] for n in ('ZC次数','RR次数','总迁移统计字节','改选次数','全块重算条目')} for k,r in rows.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
