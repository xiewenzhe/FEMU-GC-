#!/usr/bin/env python3
"""对实际全块部署源码生成独立补丁；仅修正已触发GC的全块模型调用。"""
import argparse
import difflib
from pathlib import Path

from all_blocks_patch import once


def sources(fs, inc):
    if 'const bool all_blocks_enabled=' not in fs:
        raise ValueError('来源尚未接入全块模型，拒绝修改其他选择路径')
    fs = once(fs, '#include "rrzc_all_blocks_io.h"',
              '#include "rrzc_all_blocks_io.h"\n#include "rrzc_triggered_gc.h"')
    inc = once(inc, '    const auto plan=rrzc_deadline::Select(inputs,need);', '''    // 此处已由外部GC触发进入；只修正全块模型，不修改Greedy或旧实验路径。
    const auto selection_need=all_blocks_enabled && !pair_greedy ?
        rrzc_triggered_gc::NeedForSelection(need,true) : need;
    if(all_blocks_enabled && !pair_greedy)
      Info(logger_,"触发后选区约束：轮次=%lu，GC已触发=1，原始缺口字节=%lu，评估需求字节=%lu，零缺口仍选区=%d",
          shadow_round,need.bytes,selection_need.bytes,need.known && !need.bytes);
    const auto plan=rrzc_deadline::Select(inputs,selection_need);''')
    return fs, inc


def make_patch(fs, inc):
    patch = ''
    for name, old, new in zip(('fs_zenfs.cc', 'rrzc_direct_zenfs.inc'),
                              (fs, inc), sources(fs, inc)):
        patch += ''.join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True),
            fromfile='a/fs/' + name, tofile='b/fs/' + name))
    return patch


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    patch = make_patch((args.source / 'fs_zenfs.cc').read_text(),
                       (args.source / 'rrzc_direct_zenfs.inc').read_text())
    with args.output.open('x') as out:
        out.write(patch)
