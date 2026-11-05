"""只链接小型空间收尾工具，复用本轮ZenFS对象及静态库，业务客户端不重编。"""
import json
from pathlib import Path
import subprocess
import sys
from build_all_blocks_guest import digest,old_zenfs_extracted


def build(target):
    root=Path('/home/femu/rocksdb');target=Path(target)
    identity=json.loads((target/'校准客户端身份.json').read_text())
    obj=target/'fs_all_blocks.o';client=target/'db_bench'
    assert digest(obj)==identity['ZenFS对象SHA256']
    assert digest(client)==identity['客户端SHA256']
    before={p:digest(root/p) for p in ('librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc')}
    command=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti',
        '-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX','-I.','-Iinclude',
        str(target/'space_goal_device.cc'),str(obj),'librocksdb_debug.a',
        '-Wl,--strip-debug','-Wl,-Map,'+str(target/'空间终点链接映射.txt'),
        '-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
        '-o',str(target/'space_goal')]
    with (target/'空间终点编译.log').open('w') as out:
        subprocess.run(command,cwd=root,stdout=out,stderr=subprocess.STDOUT,check=True)
    assert not old_zenfs_extracted((target/'空间终点链接映射.txt').read_text())
    assert digest(client)==identity['客户端SHA256'] and digest(obj)==identity['ZenFS对象SHA256']
    assert all(digest(root/p)==sha for p,sha in before.items())
    (target/'空间终点构建身份.json').write_text(json.dumps(dict(通过=True,
        客户端不变=True,ZenFS对象不变=True,原库不变=True,原源码不变=True,
        工具SHA256=digest(target/'space_goal'),源码SHA256=digest(target/'space_goal_device.cc'),
        ZenFS对象SHA256=digest(obj)),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':build(Path(sys.argv[1]))
