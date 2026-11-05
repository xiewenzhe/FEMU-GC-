#!/usr/bin/env python3
"""复核已记录的全块ETA，不用被ZC或Reset干预后的结果校准原源区预测。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from analyze_all_blocks import eta,require,SECOND
from gc_path_trace import fields

INFINITE=2**64-1
def wall(line):return datetime.strptime(line.split()[0],'%Y/%m/%d-%H:%M:%S.%f')
def inspect(lines):
    evidence={};results=[]
    for i,line in enumerate(lines):
        if '全块风险明细：' in line:
            row=fields(line,'全块风险明细：');evidence[int(row['轮次']),int(row['Zone']),int(row['块'])]=row
        if '全块风险代表：' not in line:continue
        representative=fields(line,'全块风险代表：')
        if representative['存在']!='1':continue
        zone=int(representative['Zone']);key=(int(representative['轮次']),zone,int(representative['代表块']))
        require(key in evidence,'缺少代表块原始证据');b=evidence[key]
        remaining=max(0,1024-int(b['RC']))
        short=eta(remaining,int(b['近期间隔']),int(b['近期增量'])) if b['近期可知']=='1' else None
        result=dict(轮次=key[0],Zone=zone,设备代际=int(b['代际']),代表块=key[2],RC=int(b['RC']),
            十秒增量=int(b['十秒增量']),短窗增量=int(b['近期增量']),短窗间隔纳秒=int(b['近期间隔']),
            当前ETA纳秒=int(representative['ETA']),仅短窗ETA纳秒=short,后续事件='业务窗口结束截尾',
            到风险帧观测秒=None,当前ETA误差秒=None,仅短窗ETA误差秒=None)
        for later in lines[i+1:]:
            if '全块业务计量截止：' in later:break
            if 'Zone 重置诊断：' in later:
                row=fields(later,'Zone 重置诊断：')
                if int(row['Zone'])==zone and row['状态']=='成功':result['后续事件']='Reset截止';break
            if '时序方案决策：' in later:
                row=fields(later,'时序方案决策：')
                if row['实际存在']=='1' and int(row['实际Zone'])==zone:
                    result['后续事件']='ZC介入，不计预测命中或误差';break
            if 'RR风险状态：' in later:
                row=fields(later,'RR风险状态：')
                if int(row['Zone'])==zone and int(row['达阈值块'])>0:
                    seconds=(wall(later)-wall(line)).total_seconds()
                    require(seconds>=0,'客机日志时间倒退')
                    result['后续事件']='同Zone未Reset前观察到阈值风险帧';result['到风险帧观测秒']=seconds
                    if result['当前ETA纳秒']!=INFINITE:result['当前ETA误差秒']=result['当前ETA纳秒']/SECOND-seconds
                    if short is not None and short!=INFINITE:result['仅短窗ETA误差秒']=short/SECOND-seconds
                    break
        results.append(result)
    return results

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    entries=[];provenance={}
    for source in sorted(a.source.glob('*-blocks/zenfs.run.log')):
        raw=source.read_text();provenance[str(source)]=hashlib.sha256(source.read_bytes()).hexdigest()
        entries.extend(dict(场景=source.parent.name,**r) for r in inspect(raw.splitlines()))
    require(entries,'没有真实全块日志')
    a.output.mkdir(exist_ok=True)
    for name,data in (('ETA逐次核验.json',entries),('ETA来源.json',provenance)):
        with (a.output/name).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    rows=[r for r in entries if r['到风险帧观测秒'] is not None]
    lines=['# 全块读趋势估计复核','',
        '只使用既有真实日志，不修改选区、速率估计或RR路径。数值是同一客机日志从预测记录到阈值风险帧的观测间隔；不混用设备时钟和主机单调时钟。',
        'ZC介入、Reset和业务结束分别截止；不能把被ZC消除的后续RR计为ETA预测准确。风险帧没有直接记录真正触发块PPA，结论限于同Zone未Reset的阈值事件。','',
        '| 场景 | Zone | 当前ETA秒 | 仅短窗ETA秒（离线） | 到风险帧秒 | 当前估计偏晚秒 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        short=r['仅短窗ETA纳秒']/SECOND if r['仅短窗ETA纳秒'] not in (None,INFINITE) else None
        lines.append(f"| {r['场景']} | {r['Zone']} | {r['当前ETA纳秒']/SECOND:.6f} | {short if short is not None else '未知'} | {r['到风险帧观测秒']:.6f} | {r['当前ETA误差秒'] if r['当前ETA误差秒'] is not None else '未知'} |")
    lines+=['','仅短窗重算是离线诊断，不是新机制实验结果。低收益场景的持续升温使十秒平均低于近期速率，原取慢速规则在这一例估晚。',
        '停读场景无增长且有限窗口没有RR；机会场景热区先被ZC，原预测被干预，不能验证其反事实到阈值时间。',
        '事件数很少，不据此校准概率、宣称普遍精度提升，或直接把仅短窗规则上线；后续至少需要包含持续、升温、停读和突发后停止的验证。','']
    with (a.output/'ETA复核.md').open('x') as f:f.write('\n'.join(lines))
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
