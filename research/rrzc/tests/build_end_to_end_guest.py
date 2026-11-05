"""新请求完成语义的独立客户端；复用计时构建，模型与原库不改。"""
from pathlib import Path
import sys
from build_phase_time_guest import build
import end_to_end_source

if __name__=='__main__':
    build(Path(sys.argv[1]),transforms=end_to_end_source,output_name='前后台重叠构建.json',smoke_overlap=True)
