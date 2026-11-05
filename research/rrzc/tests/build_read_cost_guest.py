"""同一后台重叠实验，仅增加读锁、读命令及前台记录成本观测。"""
from pathlib import Path
import sys
from build_phase_time_guest import build
import read_cost_source

if __name__=='__main__':
    build(Path(sys.argv[1]),transforms=read_cost_source,output_name='前后台重叠构建.json',smoke_overlap=True)
