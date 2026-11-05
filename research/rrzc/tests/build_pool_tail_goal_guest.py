"""仅编译空间结算工具，逐字复用尾部实验的客户端及全部ZenFS对象。"""
import json
from pathlib import Path
import subprocess
import sys
from build_all_blocks_guest import ROOT,digest


def build(target):
    identity=json.loads((target/'生命周期构建核验.json').read_text())
    plan=json.loads((target/'冻结对象清单.json').read_text())
    obj=target/'fs_colocation.o';fs=target/'协同池副本/plugin/zenfs/fs'
    before={p:digest(p) for p in (target/'db_bench',obj,ROOT/'librocksdb_debug.a')}
    assert before[target/'db_bench']==identity['程序SHA256']
    assert before[obj]==identity['冻结ZenFS对象SHA256']==plan['对象SHA256']
    assert before[ROOT/'librocksdb_debug.a']==plan['原库SHA256']
    mapping=target/'空间结算链接映射.txt'
    args=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti',
        '-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX','-I'+str(fs.parents[2]),'-I'+str(fs),
        '-I'+str(target),'-I.','-Iinclude',str(target/'pool_tail_goal_device.cc'),str(obj),
        'librocksdb_debug.a','-Wl,--strip-debug','-Wl,-Map,'+str(mapping),
        '-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
        '-o',str(target/'pool_tail_goal')]
    (target/'空间结算编译命令.json').write_text(json.dumps(args,ensure_ascii=False)+'\n')
    with (target/'空间结算编译.log').open('w') as log:
        subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    used=mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in plan['禁止旧对象'])
    assert all(digest(p)==sha for p,sha in before.items())
    (target/'空间结算构建.json').write_text(json.dumps(dict(通过=True,
        工具SHA256=digest(target/'pool_tail_goal'),源码SHA256=digest(target/'pool_tail_goal_device.cc'),
        程序SHA256=before[target/'db_bench'],对象SHA256=before[obj],原库SHA256=before[ROOT/'librocksdb_debug.a'],
        业务和ZenFS不重编=True),ensure_ascii=False,indent=2)+'\n')
    print('独立空间结算工具编译成功，业务程序、ZenFS对象和原库未改',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
