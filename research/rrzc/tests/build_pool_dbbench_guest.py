"""冻结ZenFS与模型，重编已验收db_bench客户端和ABI一致的空间探针。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT,digest
from build_all_blocks_dbbench_guest import commands
from flush_wait_source import native_command


def build(target):
    expected=json.loads((target/'expected.json').read_text())
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'原软件不同：'+name
    plan=json.loads((target/'冻结对象清单.json').read_text());payload=target/'冻结对象.tar.gz'
    assert digest(payload)==plan['压缩包SHA256']
    with tarfile.open(payload) as tar:
        members=tar.getmembers();assert len(members)==len(plan['文件']) and {m.name for m in members}==set(plan['文件'])
        for m in members:
            p=Path(m.name);assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert m.name=='fs_colocation.o' or p.parts[:4]==('协同池副本','plugin','zenfs','fs')
            dest=target/p;dest.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as source,dest.open('xb') as out:shutil.copyfileobj(source,out)
            assert digest(dest)==plan['文件'][m.name]
    overlay=target/'协同池副本';fs=overlay/'plugin/zenfs/fs';obj=target/'fs_colocation.o'
    before=digest(ROOT/'librocksdb_debug.a');assert before==plan['原库SHA256']
    sync_plan=None
    if (target/'同步重编清单.json').is_file():
        from build_pool_dbbench_sync_guest import build as build_sync
        sync_plan=build_sync(target,fs,obj)
    reused=target/'复用真实DB程序.json'
    if reused.is_file():
        identity=json.loads(reused.read_text())
        assert identity['对象SHA256']==digest(obj) and identity['原库SHA256']==before
        for name,sha in identity['程序'].items():
            source=target/(name+'.frozen');assert digest(source)==sha
            shutil.copyfile(source,target/name);(target/name).chmod(0o755)
        for name,sha in expected.items():assert digest(ROOT/name)==sha
        (target/'真实DB构建核验.json').write_text(json.dumps(dict(通过=True,未重新编译客户端=True,
            冻结ZenFS对象SHA256=digest(obj),原库SHA256=before,
            db_benchSHA256=digest(target/'db_bench'),来源=identity),ensure_ascii=False,indent=2)+'\n')
        print('已验收真实db_bench和相同ABI探针字节复用通过，未重新编译',flush=True)
        return
    dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    (target/'db_bench原生构建计划.txt').write_text(dry);compile_args,link_args=commands(dry)
    old=compile_args[compile_args.index('-o')+1]
    compile_args[compile_args.index('tools/db_bench_tool.cc')]=str(target/'校准-db_bench_tool.cc')
    compile_args[compile_args.index('-o')+1]=str(target/'客户端.o')
    compile_args[1:1]=['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)]
    compile_args+=['-g0']
    def run(args,name):
        with (target/(name+'.log')).open('w') as log:subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    if sync_plan:
        shutil.copyfile(target/'复用客户端对象/客户端.o',target/'客户端.o')
    else:run(compile_args,'客户端编译')
    flush_dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','db/flush_job.cc','db/flush_job.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    supports=[]
    for source in ('诊断-flush_job.cc','诊断-sync_point.cc','诊断-sync_point_impl.cc'):
        argv=native_command(flush_dry);argv[argv.index('db/flush_job.cc')]=str(target/source)
        output=target/(source+'.o');argv[argv.index('-o')+1]=str(output);argv+=['-g0']
        if sync_plan:shutil.copyfile(target/'复用客户端对象'/output.name,output)
        else:run(argv,source+'编译')
        supports.append(str(output))
    link_args=[str(target/'客户端.o') if s==old else 'librocksdb_debug.a' if s=='-lrocksdb_debug' else s for s in link_args]
    pos=link_args.index('librocksdb_debug.a');link_args[pos:pos]=[str(obj),*supports]
    link_args[link_args.index('-o')+1]=str(target/'db_bench')
    mapping=target/'db_bench链接映射.txt';link_args+=['-Wl,--strip-debug','-Wl,-Map,'+str(mapping)]
    run(link_args,'客户端链接')
    used=mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in (*plan['禁止旧对象'],'flush_job.o'))
    probe=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX',
        '-I'+str(overlay),'-I'+str(fs),'-I.','-Iinclude',str(target/'dbbench_space_probe.cc'),str(obj),
        'librocksdb_debug.a','-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
        '-o',str(target/'space_probe.new')]
    run(probe,'协同空间探针编译');(target/'space_probe.new').replace(target/'space_probe')
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'编译改动原软件：'+name
    assert (sync_plan is not None or digest(obj)==plan['对象SHA256']) and digest(ROOT/'librocksdb_debug.a')==before
    (target/'真实DB构建核验.json').write_text(json.dumps(dict(通过=True,冻结ZenFS对象SHA256=digest(obj),
        db_benchSHA256=digest(target/'db_bench'),原库SHA256=before,客户端源码SHA256=digest(target/'校准-db_bench_tool.cc'),
        同步交接补接=sync_plan is not None,请求及Flush对象逐字复用=sync_plan is not None,
        说明='同步模式只重编空间交接两端并重新链接，模型与原库不改；空间探针使用相同ABI'),ensure_ascii=False,indent=2)+'\n')
    print('真实db_bench与冻结协同对象链接通过，原库未修改',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
