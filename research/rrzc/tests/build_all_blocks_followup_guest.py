#!/usr/bin/env python3
"""复用已验收的ZenFS对象，只链接新增后续业务客户端。"""
import json
from pathlib import Path
import subprocess
import sys
from build_all_blocks_guest import digest,old_zenfs_extracted

ROOT=Path('/home/femu/rocksdb')
OLD=Path('/home/femu/joint-all-blocks-v4-20261025')
def main():
    target=Path(sys.argv[1]);expected=json.loads((target/'expected.json').read_text())
    for name,value in expected.items():assert digest(ROOT/name)==value,'父软件身份不符：'+name
    obj=OLD/'fs_all_blocks.o'
    command=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
        '-DROCKSDB_PLATFORM_POSIX','-I.','-Iinclude','-Iplugin/zenfs/fs',
        str(target/'all_blocks_device.cc'),str(obj),'librocksdb_debug.a','-Wl,--strip-debug',
        '-Wl,-Map,'+str(target/'链接映射.txt'),'-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2',
        '-pthread','-ldl','-lrt','-o',str(target/'all_blocks_device')]
    with (target/'客户端编译.log').open('w') as f:subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    mapping=(target/'链接映射.txt').read_text()
    assert not old_zenfs_extracted(mapping) and str(obj) in mapping,'链接了错误的ZenFS对象'
    for name,value in expected.items():assert digest(ROOT/name)==value,'链接后父软件身份变化：'+name
    (target/'复用模型核验.json').write_text(json.dumps(dict(通过=True,父软件身份=expected,
        复用对象=str(obj),客户端SHA256=digest(target/'all_blocks_device'),原库和对象保持不变=True),ensure_ascii=False,indent=2))
    print('后续客户端编译、原对象复用和链接核验通过',flush=True)

if __name__=='__main__':main()
