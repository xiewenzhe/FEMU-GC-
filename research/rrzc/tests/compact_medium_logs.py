#!/usr/bin/env python3
"""仅删除上一轮中测已归档且逐字节核验一致的解压日志副本；保留压缩原件。"""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-locality-medium-v1-20261024-results')


def digest(stream):
    h=hashlib.sha256()
    for data in iter(lambda:stream.read(1<<20),b''):h.update(data)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args();rows=[];archives={}
    state=json.loads((ROOT/'状态.json').read_text())
    if state['运行任务']:raise RuntimeError('仍有登记中的实验任务，不清理')
    for arm in sorted(ROOT.glob('*-results/full-hot-*')):
        if not arm.is_dir():continue
        archived=json.loads((arm/'阶段归档.json').read_text())
        if not archived['完整'] or archived['客机仍存活']:raise RuntimeError('归档或退出未核验')
        packs=list(arm.glob('*.tar.gz'))
        if len(packs)!=1:raise RuntimeError('归档不能唯一定位')
        pack=packs[0]
        with pack.open('rb') as stream:archives[str(pack)]=digest(stream)
        label=arm.parent.name.removesuffix('-results')
        with tarfile.open(pack,'r:gz') as saved:
            for path in sorted((arm/label).rglob('*.log')):
                if path.is_symlink() or not path.is_file():raise RuntimeError('日志不是普通文件')
                member=str(path.relative_to(arm))
                with path.open('rb') as stream:actual=digest(stream)
                restored=saved.extractfile(member)
                if restored is None:raise RuntimeError('压缩原件缺少日志：'+member)
                with restored:
                    if digest(restored)!=actual:raise RuntimeError('日志和压缩原件不一致：'+member)
                rows.append(dict(路径=str(path),归档=str(pack),归档成员=member,
                    字节=path.stat().st_size,SHA256=actual))
    record=dict(范围=str(ROOT),压缩归档SHA256=archives,已核验日志副本=rows,
        日志字节=sum(r['字节'] for r in rows),执行删除=args.execute,
        恢复='按各条记录从归档提取对应成员到该归档所在目录；原件未删除',
        保留='图表、CSV、核验JSON、配置、源码、镜像、压缩归档及外层监督日志')
    if args.execute:
        target=ROOT/'日志副本清理记录.json'
        with target.open('x') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
        for row in rows:
            path=Path(row['路径'])
            if not path.is_relative_to(ROOT) or path.suffix!='.log':raise RuntimeError('清理范围不符')
            with path.open('rb') as stream:
                if digest(stream)!=row['SHA256']:raise RuntimeError('删除前日志已变化')
            path.unlink()
    print(json.dumps(dict(核验日志数=len(rows),日志MiB=record['日志字节']/2**20,
        已删除=args.execute,原始压缩归档保留=True),ensure_ascii=False))


if __name__=='__main__':main()
