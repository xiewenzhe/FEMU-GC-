#!/usr/bin/env python3
"""仅编译独立校准客户端；原库、ZenFS对象和原db_bench源码均不修改。"""
import json
from pathlib import Path
import subprocess
import sys
from build_all_blocks_guest import digest,old_zenfs_extracted
from build_all_blocks_dbbench_guest import commands
from calibration_source import patched,without_hooks
from build_controlled_gc_guest import build as build_controlled_gc

root=Path('/home/femu/rocksdb');target=Path(sys.argv[1])
expected=json.loads((target/'expected.json').read_text())
for name,sha in expected.items():assert digest(root/name)==sha,'原软件身份不同：'+name
if (target/'启用受控GC检查').is_file() and not (target/'启用目标分配采证').is_file():build_controlled_gc(target)
if (target/'启用目标分配采证').is_file():
    from build_target_alloc_guest import build as build_target
    build_target(target)
source=root/'tools/db_bench_tool.cc'
(target/'原始-db_bench_tool.cc').write_bytes(source.read_bytes())
native=source.read_text();calibrated=patched(native)
# 移除显式实验钩子后逐字核对，防止意外改变原有键生成和IO。
anchor='  void RRZCRotating(ThreadState* thread) {'
assert native.split(anchor,1)[1]==without_hooks(calibrated.split(anchor,1)[1])
(target/'校准-db_bench_tool.cc').write_text(calibrated)
dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
    'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=root,text=True,stderr=subprocess.STDOUT)
(target/'原生Make计划.txt').write_text(dry)
compile_argv,link_argv=commands(dry)
old_object=compile_argv[compile_argv.index('-o')+1]
compile_argv[compile_argv.index('tools/db_bench_tool.cc')]=str(target/'校准-db_bench_tool.cc')
compile_argv[compile_argv.index('-o')+1]=str(target/'校准-db_bench_tool.o');compile_argv+=['-g0']
flush_trace=(target/'启用Flush分段').is_file()
flush_support=[]
if flush_trace:
    from flush_wait_source import patched as flush_patched,native_command,old_flush_extracted
    original=root/'db/flush_job.cc'
    original_hash=digest(original)
    (target/'原始-flush_job.cc').write_bytes(original.read_bytes())
    (target/'诊断-flush_job.cc').write_text(flush_patched(original.read_text()))
    flush_dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','db/flush_job.cc','db/flush_job.o'],cwd=root,text=True,stderr=subprocess.STDOUT)
    (target/'Flush原生构建计划.txt').write_text(flush_dry)
    flush_argv=native_command(flush_dry)
    flush_argv[flush_argv.index('db/flush_job.cc')]=str(target/'诊断-flush_job.cc')
    flush_argv[flush_argv.index('-o')+1]=str(target/'诊断-flush_job.o')
    flush_argv+=['-g0']
    with (target/'Flush诊断编译.log').open('w') as f:
        subprocess.run(flush_argv,cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
    # 原静态库不导出测试同步点；诊断对象沿用原生调试宏，补链接原生实现，不删除断言或同步调用。
    for name in ('sync_point.cc','sync_point_impl.cc'):
        support=root/'test_util'/name
        if not support.is_file():continue
        copied=target/('诊断-'+name);copied.write_bytes(support.read_bytes())
        argv=list(flush_argv);argv[argv.index(str(target/'诊断-flush_job.cc'))]=str(copied)
        obj_path=target/('诊断-'+name+'.o');argv[argv.index('-o')+1]=str(obj_path)
        with (target/('同步点-'+name+'.编译.log')).open('w') as f:
            subprocess.run(argv,cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
        flush_support.append(str(obj_path))
    (target/'同步点头文件.h').write_bytes((root/'test_util/sync_point.h').read_bytes())
    assert digest(original)==original_hash,'诊断编译意外修改原Flush源码'
    # 将真实部署路径的相关实现归档，函数名和同步行为以这些文件为准。
    copied={};missing=[];dest=target/'同步路径部署源码';dest.mkdir()
    for name in ('logging/logging.cc','logging/auto_roll_logger.cc','env/fs_posix.cc',
                 'env/io_posix.cc','env/io_posix.h','plugin/zenfs/fs/fs_zenfs.h',
                 'plugin/zenfs/fs/io_zenfs.h','include/rocksdb/file_system.h'):
        path=root/name
        if not path.is_file():missing.append(name);continue
        (dest/name.replace('/','__')).write_bytes(path.read_bytes());copied[name]=digest(path)
    (dest/'来源.json').write_text(json.dumps(dict(文件SHA256=copied,未找到=missing),ensure_ascii=False,indent=2)+'\n')
with (target/'客户端编译.log').open('w') as f:subprocess.run(compile_argv,cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
obj=target/'fs_all_blocks.o'
link_argv=[str(target/'校准-db_bench_tool.o') if s==old_object else 'librocksdb_debug.a' if s=='-lrocksdb_debug' else s for s in link_argv]
link_argv.insert(link_argv.index('librocksdb_debug.a'),str(obj))
if flush_trace:
    link_argv.insert(link_argv.index('librocksdb_debug.a'),str(target/'诊断-flush_job.o'))
    for support in flush_support:link_argv.insert(link_argv.index('librocksdb_debug.a'),support)
link_argv[link_argv.index('-o')+1]=str(target/'db_bench')
link_argv+=['-Wl,--strip-debug','-Wl,-Map,'+str(target/'校准链接映射.txt')]
with (target/'客户端链接.log').open('w') as f:subprocess.run(link_argv,cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
assert not old_zenfs_extracted((target/'校准链接映射.txt').read_text()),'误拉取旧ZenFS实现'
if flush_trace:
    linked=(target/'校准链接映射.txt').read_text()
    assert not old_flush_extracted(linked),'误拉取原FlushJob对象'
    (target/'Flush诊断身份.json').write_text(json.dumps(dict(原Flush源码SHA256=original_hash,
        诊断源码SHA256=digest(target/'诊断-flush_job.cc'),诊断对象SHA256=digest(target/'诊断-flush_job.o'),
        原源码保持不变=digest(original)==original_hash,原Flush对象未抽取=True),ensure_ascii=False,indent=2)+'\n')
with (target/'内部随机数回归编译.log').open('w') as f:
    subprocess.run(['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
        '-DROCKSDB_PLATFORM_POSIX','-I.','-Iinclude',str(target/'tls_seed_probe.cc'),
        'librocksdb_debug.a','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt','-Wl,--strip-debug',
        '-o',str(target/'tls_seed_probe')],cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
with (target/'内部随机数回归.log').open('w') as f:
    subprocess.run([str(target/'tls_seed_probe')],cwd=root,stdout=f,stderr=subprocess.STDOUT,check=True)
for name,sha in expected.items():assert digest(root/name)==sha,'编译改变原软件：'+name
(target/'校准客户端身份.json').write_text(json.dumps(dict(通过=True,原软件保持不变=True,
    客户端SHA256=digest(target/'db_bench'),ZenFS对象SHA256=digest(obj),
    请求方法除显式种子及顺序钩子外保持不变=True,内部随机数回归通过=True),ensure_ascii=False,indent=2)+'\n')
print('独立校准客户端编译通过，原库和ZenFS实现不变',flush=True)
