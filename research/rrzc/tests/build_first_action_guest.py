"""冻结旧副本后接首区评分，同ABI构建五单元及24窗口客户端。"""
import json
from pathlib import Path
import shutil
import sys
from build_all_blocks_guest import digest
from build_pool_tail_stream_guest import build as stream_build
from first_action_source import apply


def build(target):
    folder=target/'协同池副本/plugin/zenfs/fs'
    names=('fs_zenfs.cc','rrzc_direct_zenfs.inc','snapshot.h')
    saved=target/'首区前源码';saved.mkdir()
    for n in names:shutil.copyfile(folder/n,saved/n)
    before={n:digest(folder/n) for n in names};apply(target)
    stream_build(target)
    result=json.loads((target/'持续负载构建.json').read_text())
    result.update(首区模型版本=1,原源码=before,新源码={n:digest(folder/n) for n in names},
        首区核SHA256=digest(folder/'rrzc_first_action.h'),尾部执行=False,
        说明='新增文件热度快照及连续文件观察，独立首区净复制/回收评分；旧算法保留；原GC/RR及候选不改')
    (target/'首区模型构建.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':build(Path(sys.argv[1]))
