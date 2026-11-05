#!/usr/bin/env python3
"""新A至F的只读采证路径适配，旧采证入口与路径约束不变。"""
import argparse
import json
import os
from pathlib import Path
import re
from capture_ycsb_core import progress as core_progress, capture


def progress(result):
    point = core_progress(result)
    log = result/'verify.log'
    rows = re.findall(r'校验进度：键数=(\d+)', log.read_text(errors='replace')) if log.exists() else []
    point['校验键数'] = int(rows[-1]) if rows else 0
    return point


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('progress', 'capture', 'threads'))
    p.add_argument('result', type=Path)
    p.add_argument('target', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.result.parent != Path('/home/femu/rrzc-sst-results') or not re.fullmatch(
            r'(smoke|full)-[a-f]-(space|model)', a.result.name):
        p.error('只允许本轮A至F的明确结果目录')
    if a.action == 'progress':
        print(json.dumps(progress(a.result), ensure_ascii=False))
        return
    if os.geteuid() != 0 or a.output is None:
        p.error('采证需要客机root和独立输出目录')
    result = capture(a.result, a.output, a.target, a.action == 'threads')
    print(json.dumps(result, ensure_ascii=False))
    if not result['完整']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
