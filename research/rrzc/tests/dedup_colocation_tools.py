"""只对本日已完整归档的协同实验工具及恢复包去重；路径和字节不变。"""
import argparse
import json
from pathlib import Path
import stat
from dedup_ycsb_archived_tools import link_identical
from ycsb_af import digest,require

BASE=Path('/home/data/xwz/rrzc-baselines')
NAMES=('cases-v1','cases-v2','choice-v1','choice-v2','choice-v3','k1-v1')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true')
    parser.add_argument('--include-credit-v1',action='store_true',help='仅额外纳入已退出的首个校准失败臂')
    args=parser.parse_args();canonical={};rows=[];archives={}
    record=BASE/('协同实验工具去重-校准失败臂-20261029.json' if args.include_credit_v1 else
                 '协同实验工具去重-20261029.json')
    if args.apply:require(not record.exists(),'不覆盖去重记录')
    for name in NAMES+(('credit-v1',) if args.include_credit_v1 else ()):
        root=BASE/f'joint-controlled-colocation-{name}-20261029-results'
        stage=json.loads((root/'阶段归档.json').read_text());state=json.loads((root/'状态.json').read_text())
        require(stage['完整'] and not stage['客机仍存活'] and state['当前客机PID'] is None,'归档或退出未核验')
        archive=Path(state['镜像处理']['archive']);expected=state['镜像处理']['archive_sha256']
        require(digest(archive)==expected,'完整归档哈希不同');archives[str(archive)]=expected
        guest=root/state['标签'];plan=json.loads((root/'源码/宿主恢复清单.json').read_text())
        candidates=[(root/'源码/恢复文件.tar.gz',plan['压缩包SHA256']),
                    (guest/'恢复文件.tar.gz',plan['压缩包SHA256'])]
        for member,sha in plan['文件'].items():
            if member not in ('client/db_bench','client/校准链接映射.txt','tools/all_blocks_device',
                'tools/space_probe','tools/reserve_restore_device'):continue
            candidates.extend(((guest/'恢复文件'/member,sha),(guest/Path(member).name,sha)))
        for path,sha in candidates:
            if not path.exists():continue
            require(not path.is_symlink() and path.is_file() and digest(path)==sha,'工具副本不是已归档字节')
            s=path.stat();key=(sha,stat.S_IMODE(s.st_mode),s.st_dev,s.st_uid)
            if key not in canonical:canonical[key]=path;continue
            source=canonical[key]
            saved=link_identical(source,path,sha,apply=args.apply)
            if saved:rows.append(dict(源=str(source),目标=str(path),SHA256=sha,释放字节=saved))
    result=dict(已执行=args.apply,释放字节=sum(x['释放字节'] for x in rows),记录=rows,归档核验=archives,
        边界='只对完整归档后工具/恢复包同字节硬链接；日志、指标、图片、归档、镜像不删除，全部路径保留')
    if args.apply:record.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(已执行=args.apply,可去重条目=len(rows),释放MiB=result['释放字节']/2**20),ensure_ascii=False))


if __name__=='__main__':main()
