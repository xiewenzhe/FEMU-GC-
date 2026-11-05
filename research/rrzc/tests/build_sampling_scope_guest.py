"""独立重编采样翻译单元、复用其余对象，原库、请求、GC/RR/选区不改。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT,digest,compile_command
from build_all_blocks_dbbench_guest import commands
from all_blocks_scope_source import patched
from sst_lifetime_source import patched as client_source


def unpack(target,filename,manifest):
    archive=target/filename;assert digest(archive)==manifest['压缩包SHA256'],'组件包哈希不同'
    with tarfile.open(archive) as tar:
        members=tar.getmembers();assert len(members)==len(manifest['文件']) and {m.name for m in members}==set(manifest['文件'])
        for m in members:
            p=Path(m.name);assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            dest=target/p;dest.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as incoming,dest.open('xb') as out:shutil.copyfileobj(incoming,out)
            assert digest(dest)==manifest['文件'][m.name]


def build(target):
    assert not (target/'同步重编清单.json').exists() and not (target/'复用真实DB程序.json').exists(),'禁止混入其他重编模式'
    expected=json.loads((target/'expected.json').read_text())
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'原软件不同：'+name
    frozen=json.loads((target/'冻结对象清单.json').read_text());unpack(target,'冻结对象.tar.gz',frozen)
    plan=json.loads((target/'采样组件清单.json').read_text());unpack(target,'采样组件.tar.gz',plan)
    fs=target/'协同池副本/plugin/zenfs/fs';units=target/'采样组件/ZenFS';front=target/'采样组件/前端'
    obj=target/'fs_colocation.o';library=digest(ROOT/'librocksdb_debug.a')
    assert library==plan['原库SHA256'] and digest(obj)==plan['原ZenFS对象SHA256']
    old=target/'原逐单元复核.o'
    subprocess.run(['ld','-r','-o',str(old),*map(str,sorted(units.glob('*.o')))],check=True)
    assert digest(old)==digest(obj),'逐单元不能重建原冻结对象，拒绝混合构建'
    original={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    a,b=patched((fs/'fs_zenfs.cc').read_text(),(fs/'rrzc_direct_zenfs.inc').read_text())
    (fs/'fs_zenfs.cc').write_text(a);(fs/'rrzc_direct_zenfs.inc').write_text(b)
    # 旧前端对象必须对应当前完整源码与请求头，禁止用重编假装复用。
    original_client=(target/'校准-db_bench_tool.cc').read_text()
    (target/'既有-db_bench_tool.cc').write_text(original_client)
    assert digest(target/'既有-db_bench_tool.cc')==plan['原客户端源码SHA256']
    (target/'校准-db_bench_tool.cc').write_text(client_source(original_client))
    assert digest(target/'校准-db_bench_tool.cc')==plan['客户端源码SHA256']
    assert digest(target/'rrzc_sst_lifetime.h')==plan['计划头SHA256']
    def run(argv,name):
        (target/(name+'.命令.json')).write_text(json.dumps(argv,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('w') as log:subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    relative='plugin/zenfs/fs/fs_zenfs.cc'
    dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    argv=compile_command(dry);argv[argv.index(relative)]=str(fs/'fs_zenfs.cc')
    argv[argv.index('-o')+1]=str(units/'fs_zenfs.o')
    argv[1:1]=['-I'+str(fs.parents[2]),'-I'+str(fs),'-I'+str(target)];argv+=['-g0']
    run(argv,'采样翻译单元编译')
    for p in units.glob('*.o'):
        if p.name!='fs_zenfs.o':assert digest(p)==plan['文件']['采样组件/ZenFS/'+p.name]
    for p in front.glob('*.o'):assert digest(p)==plan['文件']['采样组件/前端/'+p.name]
    run(['ld','-r','-o',str(obj),*map(str,sorted(units.glob('*.o')))],'采样对象合并')
    dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    compile_args,link_args=commands(dry);old_front=compile_args[compile_args.index('-o')+1]
    link_args=[str(front/'客户端.o') if s==old_front else 'librocksdb_debug.a' if s=='-lrocksdb_debug' else s for s in link_args]
    pos=link_args.index('librocksdb_debug.a')
    link_args[pos:pos]=[str(obj),*[str(front/name) for name in ('诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o')]]
    link_args[link_args.index('-o')+1]=str(target/'db_bench')
    mapping=target/'采样链接映射.txt';link_args+=['-Wl,--strip-debug','-Wl,-Map,'+str(mapping)]
    run(link_args,'采样客户端链接')
    used=mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in (*frozen['禁止旧对象'],'flush_job.o'))
    probe=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX',
        '-I'+str(fs.parents[2]),'-I'+str(fs),'-I.','-Iinclude',str(target/'dbbench_space_probe.cc'),str(obj),
        'librocksdb_debug.a','-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt','-o',str(target/'space_probe')]
    run(probe,'采样空间探针链接')
    run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(fs),
        str(target/'test_all_blocks_late_full.cc'),'-o',str(target/'采样边界回归')],'采样回归编译')
    subprocess.run([str(target/'采样边界回归')],check=True,timeout=10)
    smoke=['env','RRZC_SST_LIFETIME_SMOKE=1','RRZC_SST_LIFETIME_PERSISTENT_H=1','RRZC_CAL_TLS_SEED=1',
        'RRZC_CAL_SINGLE_BACKGROUND=1','RRZC_CAL_REQUEST_TRACE='+str(target/'采样小测请求.bin'),str(target/'db_bench'),
        '--db='+str(target/'采样小测库'),'--benchmarks=fillseq,rrzcsstlifetime,readseq,verifychecksum',
        '--num=1024','--value_size=4096','--compression_type=none','--disable_wal=1','--cache_size=0','--row_cache_size=0',
        '--threads=1','--seed=20261023','--reads=1024','--writes=480']
    run(smoke,'采样普通目录小测')
    assert 'SST生命周期完成：读=256，写=480，检查点=10' in (target/'采样普通目录小测.log').read_text()
    for name,sha in original.items():
        if name not in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):assert digest(fs/name)==sha,'无关源码改变：'+name
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'原软件被修改：'+name
    raw=(target/'db_bench').read_bytes()
    for marker in ('ZENFS_RRZC_SAMPLE_PARTIAL','全块代表门控：','受控空间请求：'):
        assert marker.encode() in raw,'实际程序缺少新采样/旧同步路径'
    identity=dict(通过=True,普通目录小测通过=True,固定H普通目录小测通过=True,
        原客户端源码SHA256=plan['原客户端源码SHA256'],新客户端源码SHA256=plan['客户端源码SHA256'],
        计划头SHA256=plan['计划头SHA256'],程序SHA256=digest(target/'db_bench'),冻结ZenFS对象SHA256=digest(obj),
        原对象SHA256=plan['原ZenFS对象SHA256'],仅重编=['fs_zenfs.cc'],请求前端对象逐字复用=True,
        模型与原库未修改=True,采样边界回归通过=True,源码={p.name:digest(p) for p in fs.iterdir() if p.is_file()},
        边界='采样范围和诊断修改，重新链接为新程序；不声称旧二进制性能重复，必须重新核验Greedy')
    (target/'生命周期构建核验.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    print('独立采样单元编译、原对象复核、前端复用及普通目录最小验证通过',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
