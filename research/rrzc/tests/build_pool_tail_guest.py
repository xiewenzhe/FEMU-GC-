"""基于已验收完整机制源码重编五个ZenFS单元；业务请求及评分核原字节保留。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT,digest
from build_flush_origin_guest import command
from build_pool_dbbench_guest import build as build_client
import pool_tail_source


def build(target):
    old=json.loads((target/'生命周期构建核验.json').read_text())
    plan=json.loads((target/'冻结对象清单.json').read_text())
    stage=target/'尾部构建';stage.mkdir()
    overlay=stage/'协同池副本';shutil.copytree(target/'协同池副本',overlay)
    fs=overlay/'plugin/zenfs/fs';before={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    for name,fn in (('zbd_zenfs.h',pool_tail_source.header),('zbd_zenfs.cc',pool_tail_source.zbd),
                    ('fs_zenfs.cc',pool_tail_source.fs),('rrzc_colocation_zenfs.inc',pool_tail_source.adapter)):
        p=fs/name;p.write_text(fn(p.read_text()))
    for n in ('rrzc_pool_tail.h','rrzc_pool_tail_zenfs.inc'):shutil.copyfile(target/n,fs/n)
    def run(args,name):
        (target/(name+'.命令.json')).write_text(json.dumps(args,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('w') as log:subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    objects=[];members=set(subprocess.check_output(['ar','t',str(ROOT/'librocksdb_debug.a')],text=True).splitlines())
    for p in sorted(fs.glob('*.cc')):
        if p.stem+'.o' not in members:continue
        relative='plugin/zenfs/fs/'+p.name
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        args=command(dry,relative);args[args.index(relative)]=str(p)
        dest=stage/(p.stem+'.o');args[args.index('-o')+1]=str(dest)
        args[1:1]=['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)];args+=['-g0']
        run(args,'尾部编译-'+p.stem);objects.append(dest)
    assert len(objects)==5
    for n,sha in before.items():
        if n not in ('zbd_zenfs.h','zbd_zenfs.cc','fs_zenfs.cc','rrzc_colocation_zenfs.inc'):
            assert digest(fs/n)==sha,'无关源码发生变化：'+n
    obj=stage/'fs_colocation.o';run(['ld','-r','-o',str(obj),*map(str,objects)],'尾部对象合并')
    archive=target/'冻结对象.tar.gz';archive.rename(target/'尾部前冻结对象.tar.gz')
    files={'fs_colocation.o':obj}
    files.update({str(p.relative_to(stage)):p for p in overlay.rglob('*') if p.is_file()})
    with tarfile.open(archive,'x:gz') as tar:
        for n,p in files.items():tar.add(p,arcname=n,recursive=False)
    plan.update(对象SHA256=digest(obj),文件={n:digest(p) for n,p in files.items()},压缩包SHA256=digest(archive))
    (target/'冻结对象清单.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (target/'协同池副本').rename(target/'尾部前协同池副本')
    (target/'fs_colocation.o').rename(target/'尾部前对象.o')
    build_client(target)
    run(['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
        '-DROCKSDB_PLATFORM_POSIX','-DRRZC_COLOCATION_ADAPTER=1',
        '-I'+str(target/'协同池副本'),'-I'+str(target/'协同池副本/plugin/zenfs/fs'),
        '-I'+str(target),'-I.','-Iinclude',str(target/'pool_tail_device.cc'),str(target/'fs_colocation.o'),
        'librocksdb_debug.a','-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2',
        '-pthread','-ldl','-lrt','-o',str(target/'pool_tail_device')],'尾部安全程序编译')
    assert digest(target/'校准-db_bench_tool.cc')==old['新客户端源码SHA256']
    assert digest(target/'rrzc_sst_lifetime.h')==old['计划头SHA256']
    assert digest(ROOT/'librocksdb_debug.a')==plan['原库SHA256']
    old.update(程序SHA256=digest(target/'db_bench'),冻结ZenFS对象SHA256=digest(obj),
        尾部管理版本=1,本轮逐字复用=False,本轮重新编译=True,尾部默认关闭=True,
        边界='仅协同池零有效尾部复用、资源退出及关闭清账；全部五单元同ABI重编，请求及原库/评分核未改')
    (target/'生命周期构建核验.json').write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n')
    print('尾部管理五单元及真实客户端编译成功，请求与原库未改',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
