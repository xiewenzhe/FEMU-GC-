#!/usr/bin/env python3
"""只复用哈希核验的诊断客户端及构建证据；不修改原库，不重新编译。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reuse(target):
    plan=json.loads((target/'复用诊断客户端.json').read_text())
    source=Path(plan['客机目录'])
    assert source.parent==target.parent==Path('/home/femu') and source!=target,'复用路径非法'
    assert source.is_dir() and not source.is_symlink(),'来源目录身份不符'
    for name,sha in plan['文件SHA256'].items():
        assert Path(name).name==name,'仅允许清单中的直接文件'
        path=source/name
        assert path.is_file() and not path.is_symlink() and digest(path)==sha,'来源诊断文件身份变化：'+name
    for name in plan['文件SHA256']:
        shutil.copyfile(source/name,target/name)
        shutil.copymode(source/name,target/name)
        assert digest(target/name)==plan['文件SHA256'][name],'复制后身份不符'
    identity=json.loads((target/'校准客户端身份.json').read_text())
    assert digest(target/'db_bench')==identity['客户端SHA256'],'客户端和原构建证明不一致'
    assert digest(target/'fs_all_blocks.o')==identity['ZenFS对象SHA256'],'ZenFS对象和前轮不同'
    (target/'诊断客户端复用核验.json').write_text(json.dumps(dict(通过=True,来源=plan,
        未重新编译=True,客户端SHA256=digest(target/'db_bench')),ensure_ascii=False,indent=2)+'\n')
    print('诊断客户端及源码证据SHA256一致，未重新编译',flush=True)


if __name__=='__main__':reuse(Path(sys.argv[1]))
