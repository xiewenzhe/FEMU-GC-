#!/usr/bin/env python3
"""两阶段短测只读观察；不把预热维护误算为热读阶段结果。"""
from pathlib import Path
import json
import probe_ycsb_progress as base

ROOT=Path('/home/femu/rrzc-ycsb-results/20261017')

def progress():
    phase=(ROOT/'阶段.txt').read_text().strip() if (ROOT/'阶段.txt').exists() else '尚未开始'
    base.ROOT=ROOT/'warmup' if phase=='warmup' else ROOT
    out=base.progress()
    if phase=='evaluate' and out['阶段']=='装载':out['阶段']='热读'
    out['阶段']=phase+'/'+out['阶段']
    # 两阶段共享同一数据库的RocksDB辅助目录。
    error_log=ROOT/'aux/rrzc-ycsb/LOG'
    if error_log.exists():
        import re
        out['错误'] += [s for s in error_log.read_text(errors='replace').splitlines()
            if re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:',s)][-4:]
    return out

if __name__=='__main__':print(json.dumps(progress(),ensure_ascii=False))
