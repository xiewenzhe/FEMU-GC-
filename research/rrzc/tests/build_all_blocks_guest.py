#!/usr/bin/env python3
"""只编译修改后的ZenFS翻译单元并优先链接；不重建或覆盖原静态库。"""
from pathlib import Path
import hashlib
import json
import re
import shlex
import subprocess
import sys

ROOT=Path('/home/femu/rocksdb')
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def compile_command(dry):
    commands=[]
    for line in dry.replace('\\\n',' ').splitlines():
        if '-c plugin/zenfs/fs/fs_zenfs.cc' not in line:continue
        # Make输出先echo、mkdir，再执行编译器；只提取明确的g++命令。
        match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
        assert match,'不认识的原生编译器命令前缀'
        words=shlex.split(match.group(1))
        if '-o' in words:commands.append(words)
    assert len(commands)==1,'不能唯一确定原生翻译单元编译命令'
    return commands[0]
def old_zenfs_extracted(mapping):
    members=mapping.split('Discarded input sections',1)[0]
    return any(Path(name).name=='fs_zenfs.o' for name in re.findall(r'librocksdb_debug\.a\(([^\n)]+)\)',members))
def main():
    target=Path(sys.argv[1]);library=ROOT/'librocksdb_debug.a';before=digest(library)
    args=['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0','ROCKSDB_PLUGINS=zenfs',
        '-W','plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/fs_zenfs.o']
    dry=subprocess.check_output(args,cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    (target/'单文件构建命令.txt').write_text(dry)
    command=compile_command(dry)
    assert not any(x in command for x in (';','&&','||','|','>')),'不执行含shell操作的猜测命令'
    command[command.index('-o')+1]=str(target/'fs_all_blocks.o')
    # 沿用原ABI/宏/依赖，只覆盖调试信息生成，控制可销毁工具空间预算。
    command+=['-g0']
    with (target/'单文件编译.log').open('w') as f:subprocess.run(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (target/'fs_all_blocks.o').open('rb') as f:assert '全块风险配置'.encode() in f.read(),'新对象缺少接线标记'
    link=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
        '-DROCKSDB_PLATFORM_POSIX','-I.','-Iinclude','-Iplugin/zenfs/fs',
        str(target/'all_blocks_device.cc'),str(target/'fs_all_blocks.o'),'librocksdb_debug.a',
        '-Wl,--strip-debug','-Wl,-Map,'+str(target/'链接映射.txt'),'-lzbd','-lsnappy','-llz4','-lzstd',
        '-lz','-lbz2','-pthread','-ldl','-lrt','-o',str(target/'all_blocks_device')]
    with (target/'受控程序编译.log').open('w') as f:subprocess.run(link,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    # 映射首段列出真正抽取的archive成员；只接受新对象定义ZenFS方法。
    mapping=(target/'链接映射.txt').read_text()
    assert not old_zenfs_extracted(mapping),'误拉入原库旧ZenFS翻译单元'
    assert str(target/'fs_all_blocks.o') in mapping,'没有链接新ZenFS对象'
    assert digest(library)==before,'原库被意外改动'
    (target/'对象优先链接核验.json').write_text(json.dumps(dict(通过=True,原库SHA256=before,
        新对象SHA256=digest(target/'fs_all_blocks.o'),程序SHA256=digest(target/'all_blocks_device'),
        旧对象未抽取=True,方式='原生编译参数单文件编译，新对象先于未修改静态库链接'),ensure_ascii=False,indent=2))
    print('单文件编译和对象优先链接通过，原静态库未改变',flush=True)

if __name__=='__main__':main()
