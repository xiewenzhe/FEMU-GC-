#!/usr/bin/env python3
"""沿用原生Make参数编译db_bench客户端，优先链接已验收的全块ZenFS对象。"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from build_all_blocks_guest import digest,old_zenfs_extracted

ROOT=Path('/home/femu/rocksdb')
OBJECT=Path('/home/femu/joint-all-blocks-v4-20261025/fs_all_blocks.o')
def commands(dry):
    compile_rows=[];link_rows=[]
    for line in dry.replace('\\\n',' ').splitlines():
        if 'g++ ' not in line:continue
        match=re.search(r'(?:^|&&\s+|;\s*)((?:ccache\s+)?g\+\+\s+.*)$',line)
        if not match:continue
        words=shlex.split(match.group(1))
        if '-c' in words and 'tools/db_bench_tool.cc' in words:compile_rows.append(words)
        if '-o' in words and words[words.index('-o')+1]=='db_bench':link_rows.append(words)
    assert len(compile_rows)==len(link_rows)==1,'不能唯一识别原生客户端编译与链接命令'
    for words in (compile_rows[0],link_rows[0]):
        assert not any(s in words for s in ('&&','||',';','|','>')),'拒绝执行shell组合命令'
    return compile_rows[0],link_rows[0]

def main():
    target=Path(sys.argv[1]);expected=json.loads((target/'expected.json').read_text())
    for name,value in expected.items():assert digest(ROOT/name)==value,'父软件身份不符：'+name
    dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    (target/'原生Make计划.txt').write_text(dry)
    compile_argv,link_argv=commands(dry)
    old_object=compile_argv[compile_argv.index('-o')+1]
    compile_argv[compile_argv.index('-o')+1]=str(target/'db_bench_tool.o');compile_argv+=['-g0']
    with (target/'客户端编译.log').open('w') as f:subprocess.run(compile_argv,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert old_object in link_argv,'链接命令没有包含原客户端对象'
    link_argv=[str(target/'db_bench_tool.o') if s==old_object else s for s in link_argv]
    # 原生Make使用-l形式；明确绑定已核验静态库，避免选到同目录其他共享库。
    link_argv=['librocksdb_debug.a' if s=='-lrocksdb_debug' else s for s in link_argv]
    lib=link_argv.index('librocksdb_debug.a');link_argv.insert(lib,str(OBJECT))
    link_argv[link_argv.index('-o')+1]=str(target/'db_bench')
    link_argv+=['-Wl,--strip-debug','-Wl,-Map,'+str(target/'链接映射.txt')]
    with (target/'客户端链接.log').open('w') as f:subprocess.run(link_argv,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    probe=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX',
        '-I.','-Iinclude',str(target/'dbbench_space_probe.cc'),str(OBJECT),'librocksdb_debug.a',
        '-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt','-o',str(target/'space_probe')]
    with (target/'空间探针编译.log').open('w') as f:subprocess.run(probe,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    mapping=(target/'链接映射.txt').read_text()
    assert not old_zenfs_extracted(mapping) and str(OBJECT) in mapping,'未正确使用全块模型对象'
    for name,value in expected.items():assert digest(ROOT/name)==value,'构建意外改变父软件：'+name
    raw=(target/'db_bench').read_bytes()
    for marker in ('RRZC批次完成：','RRZC调用延迟：','全块风险代表：'):
        assert marker.encode() in raw,'缺少实际接线：'+marker
    (target/'客户端身份.json').write_text(json.dumps(dict(通过=True,父软件保持不变=True,
        客户端SHA256=digest(target/'db_bench'),全块ZenFS对象SHA256=digest(OBJECT)),ensure_ascii=False,indent=2))
    print('db_bench客户端与原全块ZenFS对象链接通过',flush=True)

if __name__=='__main__':main()
