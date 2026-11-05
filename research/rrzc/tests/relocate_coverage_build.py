#!/usr/bin/env python3
"""将本轮新生成且已退出的构建镜像完整迁至HDD；原入口保留符号链接。"""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import shutil

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(4<<20),b''):h.update(data)
    return h.hexdigest()


def relocate(label,tag):
    root=Path('/home/data/xwz/rrzc-baselines')
    import re
    assert re.fullmatch('joint-dbbench8-rotating-v[1-9]-20261024',label)
    assert tag in ('prepare','smoke-hot-space','smoke-hot-model','full-hot-space','full-hot-model')
    source=root/(label+'-'+tag+'.qcow2')
    state=json.loads((root/(label+'-results')/tag/'阶段归档.json').read_text())
    assert state['完整'] and not state['客机仍存活']
    if source.is_symlink():
        assert source.is_file();return
    commands=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    assert str(root)+'/' not in commands and source.is_file() and not source.is_symlink()
    folder=Path('/home/HDD/xwz/rrzc-build-archives');folder.mkdir(parents=True,exist_ok=True)
    target=folder/source.name;assert not target.exists()
    original=digest(source)
    subprocess.run(['cp','--sparse=always','--preserve=timestamps','--no-clobber',str(source),str(target)],check=True)
    assert digest(target)==original
    # 完整字节核对后原路径换为链接；模型、镜像内容和backing绝对路径均不改。
    source.unlink();source.symlink_to(target)
    assert source.is_file() and digest(source)==original
    (root/(label+'-results')/tag/'镜像迁移.json').write_text(json.dumps(dict(原路径=str(source),
        当前存放=str(target),SHA256=original,核验='完整SHA256一致，原路径保留链接；无运行中客机',
        回退='客机退出后将目标镜像复制回原路径并替换链接；迁移未删除任何镜像内容'),ensure_ascii=False,indent=2))
    print('本轮构建镜像完整迁至HDD，原路径可用：'+str(source))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',choices=tuple('v'+str(n) for n in range(1,10)),default='v4')
    args=parser.parse_args();relocate('joint-dbbench8-rotating-'+args.version+'-20261024','prepare')


def relocate_failed_evidence(version):
    # 只迁移本轮已经失败并完整退出的准备记录，不清理用户其他项目。
    assert version in ('v5','v6','v7','v8')
    label='joint-dbbench8-rotating-'+version+'-20261024'
    source=Path('/home/data/xwz/rrzc-baselines')/(label+'-results')
    if source.is_symlink():return
    state=json.loads((source/'prepare/阶段归档.json').read_text())
    assert state['完整'] and not state['客机仍存活'] and not state['业务成功']
    target=Path('/home/HDD/xwz/rrzc-build-archives')/source.name
    assert not target.exists()
    files=sorted(p for p in source.rglob('*') if p.is_file())
    assert not any(p.is_symlink() for p in source.rglob('*'))
    hashes={str(p.relative_to(source)):digest(p) for p in files}
    shutil.copytree(source,target)
    assert all(digest(target/name)==value for name,value in hashes.items())
    for path in files:path.unlink()
    for path in sorted((p for p in source.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):path.rmdir()
    source.rmdir();source.symlink_to(target,target_is_directory=True)
    (target/'证据目录迁移.json').write_text(json.dumps(dict(原路径=str(source),当前存放=str(target),
        文件SHA256=hashes,核验='全部文件逐项SHA256一致，原路径链接保留，实验已退出'),ensure_ascii=False,indent=2))
    print('失败准备证据完整迁至HDD：'+str(source))


if __name__=='__main__':main()
