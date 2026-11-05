#!/usr/bin/env python3
"""只读检查本轮YCSB进度与首个后台错误；不杀进程、不重试。"""
import json
from pathlib import Path
import re
import time

ROOT=Path('/home/femu/rrzc-ycsb-results/20261017')

def live_check_time(root):
    # 业务退出后维护线程已结束；独立只读校验期间不再要求它继续发帧。
    # 原业务风险记录仍完整检查，不能因退出标志清除已有健康错误。
    return None if (root/'business.exit').exists() else time.monotonic_ns()

def progress():
    if not (ROOT/'ycsb.log').exists():return dict(阶段='尚未开始',操作数=None,错误=[])
    text=(ROOT/'ycsb.log').read_text(errors='replace')
    loaded='Load runtime(sec):' in text
    section=text.split('Load runtime(sec):',1)[1] if loaded else text
    rows=re.findall(r'^.*? (\d+) sec: (\d+) operations;.*$',section,re.M)
    out=dict(阶段=('业务' if rows else '装载后等待') if loaded else '装载',
        操作数=int(rows[-1][1]) if rows else None,最近状态=rows[-1] if rows else None,错误=[])
    log=ROOT/'aux/rrzc-ycsb/LOG'
    if log.exists():
        out['错误']=[s for s in log.read_text(errors='replace').splitlines()
            if re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:',s)][-4:]
    before=ROOT/'logs.before.txt'
    if before.exists():
        old=set(before.read_text().splitlines())
        logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
        if len(logs)==1:
            out['维护日志路径']=str(logs[0])
            out['维护日志字节']=logs[0].stat().st_size
            # 限制单次诊断读量；完整验证在结束后进行。
            with logs[0].open('rb') as f:
                f.seek(max(0,logs[0].stat().st_size-(2<<20)))
                tail=f.read().decode(errors='replace')
            out['最近维护']=[s for s in tail.splitlines() if 'ZC 统计：' in s or 'RR 统计：' in s][-2:]
            if Path(__file__).with_name('rrzc_rr_health.py').is_file():
                from rrzc_rr_health import inspect
                health=inspect(tail,strict=False,now_ns=live_check_time(ROOT))
                out['RR健康检查']=health
                out['错误'].extend(health['错误'])
            if out['维护日志字节']>=256<<20:out['错误'].append('维护日志达到文件大小保护上限')
    return out

if __name__=='__main__':print(json.dumps(progress(),ensure_ascii=False))
