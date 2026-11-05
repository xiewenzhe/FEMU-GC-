#!/usr/bin/env python3
"""仅在新建临时客机恢复已归档软件；不重编库、不导入模拟NAND状态。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1<<20),b''):h.update(data)
    return h.hexdigest()


def restore(target):
    assert target.parent==Path('/home/femu') and target.is_dir() and not target.is_symlink()
    plan=json.loads((target/'宿主恢复清单.json').read_text())
    payload=target/'恢复文件.tar.gz'
    assert digest(payload)==plan['压缩包SHA256'],'宿主恢复包哈希不符'
    extracted=target/'恢复文件';extracted.mkdir()
    with tarfile.open(payload) as tar:
        members=tar.getmembers()
        assert {m.name for m in members}==set(plan['文件']),'恢复包成员清单不符'
        assert all(m.isfile() and Path(m.name).parts[0] in ('native','client','tools') and
                   '..' not in Path(m.name).parts and not Path(m.name).is_absolute() for m in members)
        for member in members:
            dest=extracted/member.name;dest.parent.mkdir(exist_ok=True,parents=True)
            with tar.extractfile(member) as source,dest.open('xb') as out:shutil.copyfileobj(source,out)
            dest.chmod(member.mode&0o777)
    for name,sha in plan['文件'].items():assert digest(extracted/name)==sha,'恢复文件哈希不符：'+name
    native=Path('/home/femu/rocksdb');expected=json.loads((target/'expected.json').read_text())
    repair=plan['原软件恢复'];backup=target/'恢复前源码';backup.mkdir()
    # 仅列出的历史源码可恢复；原库、原db_bench等未打包项必须事先完全一致。
    for name,sha in expected.items():
        if name not in repair:assert digest(native/name)==sha,'构建检查点原软件不同：'+name
    before={}
    for name,member in repair.items():
        assert Path(name).parent.as_posix()=='plugin/zenfs/fs','只允许直接恢复列出的ZenFS源码'
        path=native/name;source=extracted/member
        assert digest(source)==expected[name],'恢复源码与已验收期望不同'
        before[name]=digest(path) if path.exists() else None
        if path.exists():shutil.copyfile(path,backup/Path(name).name)
        shutil.copyfile(source,path)
    for name,sha in expected.items():assert digest(native/name)==sha,'恢复后原软件不一致：'+name
    # 直接放入新实验目录；沿用旧客户端的全部对象及构建身份。
    for member in plan['文件']:
        if not member.startswith(('client/','tools/')):continue
        source=extracted/member;dest=target/Path(member).name
        if dest.exists():
            assert digest(dest)==digest(source),'不覆盖新实验内不同文件：'+str(dest)
        else:shutil.copyfile(source,dest);shutil.copymode(source,dest)
    identity=json.loads((target/'校准客户端身份.json').read_text())
    assert digest(target/'db_bench')==identity['客户端SHA256']
    assert digest(target/'fs_all_blocks.o')==identity['ZenFS对象SHA256']
    (target/'宿主恢复核验.json').write_text(json.dumps(dict(通过=True,恢复前源码=before,
        已验收客户端SHA256=digest(target/'db_bench'),未重新编译=True,
        边界='只恢复软件身份；Zone、WP、RC及RR状态随后独立重建，不称NAND检查点'),ensure_ascii=False,indent=2)+'\n')
    print('宿主归档软件恢复及全部原库身份核验通过，未重新编译',flush=True)


if __name__=='__main__':restore(Path(sys.argv[1]))
