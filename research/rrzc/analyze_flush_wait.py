"""将真实Flush分段时点配对；不同同步区间分开，拒绝缺失或倒序记录。"""
import json
from pathlib import Path
import re

PATTERN=re.compile(r'Flush分段：任务=(\d+)，文件=(\d+)，阶段=([^，]+)，纳秒=(\d+)')
PAIRS={'构建':'构建前/构建后','日志刷新':'日志刷新前/日志刷新后',
       '目录同步':'目录同步前/目录同步后','数据库锁':'数据库锁前/数据库锁后'}


def parse(text):
    jobs={}
    for job,file,phase,ns in PATTERN.findall(text):
        row=jobs.setdefault(int(job),dict(文件=int(file),阶段={}))
        if row['文件']!=int(file) or phase in row['阶段']:
            raise ValueError('Flush同任务文件不一致或阶段重复')
        row['阶段'][phase]=int(ns)
    result=[]
    for job,row in sorted(jobs.items()):
        phases=row['阶段'];spans={}
        if not {'构建前','构建后','日志刷新前','日志刷新后','数据库锁前','数据库锁后'}<=set(phases):
            raise ValueError('Flush关键诊断阶段缺失')
        for name,pair in PAIRS.items():
            a,b=pair.split('/')
            if a not in phases and b not in phases:
                continue
            if a not in phases or b not in phases or phases[b]<phases[a]:
                raise ValueError('Flush同步区间缺失或时间倒退')
            spans[name]=(phases[b]-phases[a])/1e9
        ordered=['构建前','构建后','日志刷新前','日志刷新后','目录同步前','目录同步后','数据库锁前','数据库锁后']
        times=[phases[p] for p in ordered if p in phases]
        if times!=sorted(times):raise ValueError('Flush阶段跨区间乱序')
        result.append(dict(任务=job,文件=row['文件'],分段秒=spans,时点纳秒=phases))
    if not result:raise ValueError('缺少真实Flush诊断记录')
    return result


def analyze(source,output):
    report={}
    for tag in ('g1','g2'):
        rows=parse((source/tag/'run.log').read_text())
        summary={name:dict(总秒=sum(r['分段秒'].get(name,0) for r in rows),
            最大秒=max(r['分段秒'].get(name,0) for r in rows)) for name in PAIRS}
        report[tag]=dict(Flush数=len(rows),汇总=summary,逐任务=rows)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return {tag:r['汇总'] for tag,r in report.items()}
