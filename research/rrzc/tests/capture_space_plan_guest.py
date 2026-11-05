#!/usr/bin/env python3
"""空间方案三组的YCSB采证入口；复用已验证的只读采证主体，不改变旧入口允许路径。"""
import argparse
import json
import os
from pathlib import Path
import re

# 部署时本入口命名capture_ycsb_guest.py，原主体另存capture_ycsb_core.py。
# 宿主测试直接导入本文件时，沿用仓库中的原主体。
try:
    from capture_ycsb_core import progress, capture
except ModuleNotFoundError as error:
    if error.name!='capture_ycsb_core':raise
    from capture_ycsb_guest import progress, capture


def valid_path(path):
    return path.parent==Path('/home/femu/rrzc-sst-results') and bool(
        re.fullmatch(r'(smoke|short)-(greedy|space|model)',path.name))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('progress','capture','threads'))
    parser.add_argument('result',type=Path);parser.add_argument('target',type=Path)
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    if not valid_path(args.result):parser.error('仅允许本轮小测和短对照的明确结果路径')
    if args.action=='progress':print(json.dumps(progress(args.result),ensure_ascii=False));return
    if os.geteuid()!=0 or args.output is None:parser.error('采证需要客机root及独立输出目录')
    result=capture(args.result,args.output,args.target,args.action=='threads')
    print(json.dumps(result,ensure_ascii=False))
    if not result['完整']:raise SystemExit(1)


if __name__=='__main__':main()
