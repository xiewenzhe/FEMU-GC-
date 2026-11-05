#!/usr/bin/env python3
"""只复用两轮已结束YCSB中同字节工具的硬链接；不删日志、镜像或文件路径。"""
import argparse
import json
from pathlib import Path
from dedup_ycsb_archived_tools import BASE, GUEST, REFERENCE, TOOLS, link_identical
from ycsb_af import digest, require

ROOTS = tuple(BASE/n for n in ('joint-ycsb8-f4g-v1-20261023-results',
                               'joint-ycsb8-noboundary-v1-20261023-results'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((BASE/'joint-ycsb8-f-v2-20261023-results/同库身份.json').read_text())
    report = ROOTS[1]/'dbbench准备工具去重.json'
    require(not args.apply or not report.exists(), '记录已存在，不重复替换')
    rows = []
    for root in ROOTS:
        state = json.loads((root/'状态.json').read_text())
        require(state.get('当前客机PID') is None, '来源仍有客机，不处理')
        for stage in sorted(root.iterdir()):
            if not (stage/'阶段归档.json').is_file():
                continue
            status = json.loads((stage/'阶段归档.json').read_text())
            if not status['完整'] or status['客机仍存活']:
                continue
            for name in TOOLS:
                target = stage/GUEST/name
                if not target.is_file():
                    continue
                expected = manifest['/home/femu/'+GUEST+'/'+name]
                saved = link_identical(REFERENCE/name, target, expected, apply=args.apply)
                rows.append(dict(目标=str(target), SHA256=expected, 可释放字节=saved))
    data = dict(已执行=args.apply, 记录=rows, 估计释放字节=sum(r['可释放字节'] for r in rows),
                边界='仅完整归档且无客机阶段，内容及路径不变；取消中的不完整阶段不处理')
    if args.apply:
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
