"""恢复已验收的协同对象和头文件，只编译边界客户端；不重编选择器或修改原库。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT,digest


def build(target):
    plan=json.loads((target/'冻结对象清单.json').read_text())
    payload=target/'冻结对象.tar.gz';assert digest(payload)==plan['压缩包SHA256']
    library=ROOT/'librocksdb_debug.a';before=digest(library)
    assert before==plan['原库SHA256'],'软件检查点库与已验收对象不匹配'
    with tarfile.open(payload) as tar:
        members=tar.getmembers()
        assert {m.name for m in members}==set(plan['文件']) and len(members)==len(plan['文件'])
        for m in members:
            p=Path(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert m.name=='fs_colocation.o' or p.parts[:4]==('协同池副本','plugin','zenfs','fs')
            out=target/p;out.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as source,out.open('xb') as dest:shutil.copyfileobj(source,dest)
            assert digest(out)==plan['文件'][m.name],'冻结副本哈希不同：'+m.name
    overlay=target/'协同池副本';fs=overlay/'plugin/zenfs/fs';obj=target/'fs_colocation.o'
    mapping=target/'boundary.map'
    command=['g++','-O2','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
        '-DROCKSDB_PLATFORM_POSIX','-DRRZC_COLOCATION_ADAPTER','-I'+str(overlay),'-I'+str(fs),
        '-I'+str(target),'-I.','-Iinclude','-Iplugin/zenfs/fs',str(target/'cover_boundary_device.cc'),
        str(obj),'librocksdb_debug.a','-Wl,--strip-debug','-Wl,-Map,'+str(mapping),
        '-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
        '-o',str(target/'colocation_credit')]
    with (target/'边界客户端编译.log').open('w') as log:
        subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    used=mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in plan['禁止旧对象'])
    assert digest(obj)==plan['对象SHA256'] and digest(library)==before
    (target/'边界构建核验.json').write_text(json.dumps(dict(通过=True,未重新编译ZenFS=True,
        原库SHA256=before,对象SHA256=digest(obj),程序SHA256=digest(target/'colocation_credit'),
        客户端源码SHA256=digest(target/'cover_boundary_device.cc'),两种选择器同程序=True),ensure_ascii=False,indent=2)+'\n')
    print('冻结ZenFS对象及原库身份一致；仅边界客户端编译链接通过',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
