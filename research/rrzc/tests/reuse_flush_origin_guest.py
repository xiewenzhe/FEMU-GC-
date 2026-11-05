"""逐字复用已验收来源程序和全部同ABI源码/对象，不执行编译。"""
import json
from pathlib import Path
import shutil
import sys
import tarfile
from restore_calibration_guest import digest


def unpack(payload, manifest, dest, allowed):
    assert digest(payload) == manifest['压缩包SHA256'], '复用包哈希不同'
    with tarfile.open(payload) as tar:
        members = tar.getmembers()
        assert len(members) == len(manifest['文件']) and {m.name for m in members} == set(manifest['文件'])
        for m in members:
            path = Path(m.name)
            assert m.isfile() and not path.is_absolute() and '..' not in path.parts and allowed(path)
        dest.mkdir()
        for m in members:
            path = dest/m.name; path.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src, path.open('xb') as out:shutil.copyfileobj(src,out)
            path.chmod(m.mode & 0o777)
            assert digest(path) == manifest['文件'][m.name], '复用成员哈希不同：'+m.name


def restore(target, native=Path('/home/femu/rocksdb')):
    plan = json.loads((target/'Flush来源复用清单.json').read_text())
    expected = json.loads((target/'expected.json').read_text())
    for name, sha in expected.items():assert digest(native/name) == sha, '原软件变化：'+name
    assert digest(native/'librocksdb_debug.a') == plan['原库SHA256']
    bundle = json.loads((target/'冻结对象清单.json').read_text())
    assert bundle['对象SHA256'] == plan['对象SHA256'] and bundle['原库SHA256'] == plan['原库SHA256']
    objects = target/'来源冻结对象'
    unpack(target/'冻结对象.tar.gz', bundle, objects,
           lambda p:p.as_posix()=='fs_colocation.o' or p.parts[:4]==('协同池副本','plugin','zenfs','fs'))
    programs = target/'来源冻结程序'
    names = {'db_bench','space_probe','flush_metadata_probe','生命周期构建核验.json',
             '校准-db_bench_tool.cc','诊断-flush_job.cc','rrzc_sst_lifetime.h'}
    assert set(plan['文件']) == names
    unpack(target/'Flush来源复用.tar.gz', plan, programs, lambda p:len(p.parts)==1 and p.name in names)
    build = json.loads((programs/'生命周期构建核验.json').read_text())
    assert build['通过'] and build['元数据版本']==11 and build['ZenFS完整五单元重编']
    assert digest(programs/'db_bench') == plan['程序SHA256'] == build['程序SHA256']
    assert digest(programs/'校准-db_bench_tool.cc') == plan['请求源码SHA256'] == build['新客户端源码SHA256']
    assert digest(objects/'fs_colocation.o') == plan['对象SHA256'] == build['冻结ZenFS对象SHA256']
    # 原恢复客户端在实验目录内留痕；不覆盖客机原生库或原生程序。
    backup = target/'来源复用前'; backup.mkdir()
    for name in names - {'生命周期构建核验.json'}:
        dest = target/name
        if dest.exists():shutil.copyfile(dest,backup/name)
        shutil.copyfile(programs/name,dest);shutil.copymode(programs/name,dest)
    shutil.copyfile(objects/'fs_colocation.o',target/'fs_colocation.o')
    shutil.copytree(objects/'协同池副本',target/'协同池副本')
    build.update(本轮重新编译=False, 本轮逐字复用=True, 来源=plan['来源'],
        本轮重新运行普通目录小测=False,
        边界='构建及普通目录小测为来源已验收证据；本轮哈希复用，设备探针及两次业务另行实跑')
    (target/'生命周期构建核验.json').write_text(json.dumps(build,ensure_ascii=False,indent=2)+'\n')
    (target/'Flush来源复用核验.json').write_text(json.dumps(dict(通过=True,清单=plan),ensure_ascii=False,indent=2)+'\n')
    print('已验收来源程序、同ABI对象和请求源码逐字复用；未重新编译',flush=True)


if __name__=='__main__':restore(Path(sys.argv[1]))
