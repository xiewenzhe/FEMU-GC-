"""复用已完成Greedy的真实重叠程序；原库不改，不重新编译或重跑Greedy。"""
import json
from pathlib import Path
import shutil
import sys
import tarfile
from restore_calibration_guest import digest


def restore(target):
    plan=json.loads((target/'重叠程序复用清单.json').read_text())
    payload=target/'重叠程序复用.tar.gz'
    assert digest(payload)==plan['压缩包SHA256']
    root=target/'重叠程序来源';root.mkdir()
    with tarfile.open(payload) as tar:
        members=tar.getmembers()
        assert len(members)==len(plan['文件']) and {m.name for m in members}==set(plan['文件'])
        for m in members:
            p=Path(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert len(p.parts)==1 or p.parts[:4]==('协同池副本','plugin','zenfs','fs')
            dst=root/p;dst.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src,dst.open('xb') as out:shutil.copyfileobj(src,out)
            dst.chmod(m.mode & 0o777)
            assert digest(dst)==plan['文件'][m.name]
    proof=json.loads((root/'前后台重叠构建.json').read_text())
    assert proof['通过'] and proof['前后台重叠']
    assert digest(root/'db_bench')==proof['程序SHA256']
    assert digest(root/'计时ZenFS.o')==proof['ZenFS对象SHA256']
    assert digest(Path('/home/femu/rocksdb/librocksdb_debug.a'))==proof['原库SHA256']
    (target/'db_bench').rename(target/'重叠复用前-db_bench')
    shutil.copyfile(root/'db_bench',target/'db_bench');(target/'db_bench').chmod(0o755)
    (target/'协同池副本').rename(target/'重叠复用前副本')
    shutil.copytree(root/'协同池副本',target/'协同池副本')
    for n in ('计时ZenFS.o','校准-db_bench_tool.cc','前后台重叠构建.json'):
        shutil.copyfile(root/n,target/n)
    (target/'重叠程序复用核验.json').write_text(json.dumps(dict(通过=True,程序SHA256=proof['程序SHA256'],
        来源=plan['来源'],来源归档SHA256=plan['来源归档SHA256'],不重新编译=True,
        边界='复用软件及成功Greedy证据；设备与RC仍独立Reset重建，非内存快照'),ensure_ascii=False,indent=2)+'\n')
    print('前后台重叠程序和同ABI源码逐字复用通过，不重新编译、不重跑Greedy',flush=True)


if __name__=='__main__':restore(Path(sys.argv[1]))
