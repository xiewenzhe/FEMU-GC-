#!/usr/bin/env python3
"""仅对明确实验中已归档、同哈希的工具副本复用inode；不删路径、日志或镜像。"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from ycsb_af import digest, require

BASE = Path('/home/data/xwz/rrzc-baselines')
GUEST = 'joint-ycsb32-af-v3-20261023'
REFERENCE = BASE/'joint-ycsb8-f-v2-20261023-results/build'/GUEST
ROOTS = tuple(BASE/n for n in ('joint-ycsb8-f-v2-20261023-results',
    'joint-ycsb32-stream-v3-20261023-results', 'joint-ycsb32-af-v3-20261023-results'))
TOOLS = ('verify_ycsb','sst_device_probe','quick_device_probe')


def link_identical(source, target, expected, *, apply=False):
    require(not source.is_symlink() and not target.is_symlink(), '拒绝对软链接去重')
    a,b=source.stat(),target.stat()
    require(stat.S_ISREG(a.st_mode) and stat.S_ISREG(b.st_mode), '不是普通文件')
    require(a.st_uid==b.st_uid==os.getuid() and a.st_dev==b.st_dev and
            stat.S_IMODE(a.st_mode)==stat.S_IMODE(b.st_mode), '归属、设备或权限不一致')
    require(digest(source)==expected==digest(target), '工具字节不一致，不作替换')
    if a.st_ino==b.st_ino:
        return 0
    saved=b.st_blocks*512 if b.st_nlink==1 else 0
    if apply:
        temporary=target.with_name(target.name+'.去重临时链接')
        require(not temporary.exists(), '临时链接已存在')
        # 已结束且归档通过的工具保持只读；原子替换目录项，不改变内容或任何日志。
        os.link(source,temporary)
        try:
            require(digest(target)==expected and target.stat().st_ino==b.st_ino,
                    '替换前目标发生变化')
            temporary.replace(target)
        finally:
            if temporary.exists():temporary.unlink()
        require(target.stat().st_ino==source.stat().st_ino and digest(target)==expected,
                '硬链接替换后核验失败')
    return saved


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    report_path=ROOTS[0]/'工具副本硬链接去重.json'
    if args.apply:require(not report_path.exists(),'去重记录已存在，不覆盖')
    manifest=json.loads((ROOTS[0]/'同库身份.json').read_text())
    references={n:manifest['/home/femu/'+GUEST+'/'+n] for n in TOOLS}
    for name,h in references.items():require(digest(REFERENCE/name)==h,'参考工具身份不符')
    rows=[]
    for root in ROOTS:
        for stage in sorted(root.iterdir()):
            status=stage/'阶段归档.json'
            if not status.is_file():continue
            checked=json.loads(status.read_text())
            if not checked['完整'] or checked['客机仍存活']:continue
            for name in TOOLS:
                target=stage/GUEST/name
                if not target.is_file():continue
                # 某旧试次没有64Zone新探针；原32Zone/256Zone工具均按真实哈希核对。
                if digest(target)!=references[name]:continue
                saved=link_identical(REFERENCE/name,target,references[name],apply=args.apply)
                if saved:rows.append(dict(目标=str(target),可释放字节=saved,SHA256=references[name]))
    report=dict(已执行=args.apply,同字节硬链接记录=rows,估计释放字节=sum(r['可释放字节'] for r in rows),
                边界='只处理已完整归档阶段的三个工具；日志、文件路径、镜像、软件字节不变')
    if args.apply:
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
