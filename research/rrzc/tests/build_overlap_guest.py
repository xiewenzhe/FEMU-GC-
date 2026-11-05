"""重用已核验编译流程，构建独立前后台重叠客户端。"""
from pathlib import Path
import sys
from build_phase_time_guest import build
import overlap_source

if __name__=='__main__':
    build(Path(sys.argv[1]),transforms=overlap_source,output_name='前后台重叠构建.json',smoke_overlap=True)
