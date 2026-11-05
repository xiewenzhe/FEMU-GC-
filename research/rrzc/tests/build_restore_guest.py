#!/usr/bin/env python3
"""按原生参数重编全部ZenFS fs目录翻译单元，组合后优先链接；原静态库不变。"""
from pathlib import Path
import json
import re
import shlex
import subprocess
import sys
import build_all_blocks_guest as helper
import build_all_blocks_dbbench_guest as client


def main():
    target=Path(sys.argv[1]);root=helper.ROOT;library=root/'librocksdb_debug.a';before=helper.digest(library)
    expected=json.loads((target/'expected.json').read_text())
    for name,value in expected.items():assert helper.digest(root/name)==value,'修复编译前身份不符：'+name
    directory=target/'zenfs-objects';directory.mkdir();objects=[]
    archive_members={Path(x).name for x in subprocess.check_output(['ar','t',str(library)],text=True).splitlines()}
    # 新增非虚成员不改变对象布局；所有依赖该头文件的ZenFS实现统一重编，避免混用旧实现。
    for source in sorted((root/'plugin/zenfs/fs').glob('*.cc')):
        # 仅重编原库实际启用的实现，不引入未启用的Prometheus等可选依赖。
        if source.stem+'.o' not in archive_members:continue
        relative=str(source.relative_to(root));native=relative[:-3]+'.o'
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,native],cwd=root,text=True,stderr=subprocess.STDOUT)
        choices=[]
        for line in dry.replace('\\\n',' ').splitlines():
            match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if not match:continue
            argv=shlex.split(match[1])
            if '-c' in argv and relative in argv:choices.append(argv)
        assert len(choices)==1,'不能识别原生编译命令：'+relative
        command=choices[0];assert not any(x in command for x in ('&&',';','|','>'))
        obj=directory/(source.stem+'.o');command[command.index('-o')+1]=str(obj);command+=['-g0']
        with (directory/(source.stem+'.log')).open('w') as out:
            subprocess.run(command,cwd=root,stdout=out,stderr=subprocess.STDOUT,check=True)
        objects.append(obj)
    assert {'fs_zenfs.o','io_zenfs.o','zbd_zenfs.o'}<={x.name for x in objects},'缺少实际ZenFS核心实现'
    combined=target/'fs_all_blocks.o'
    subprocess.run(['ld','-r','-o',str(combined),*map(str,objects)],check=True)
    client.OBJECT=combined;client.main()
    for name,source in [('all_blocks_device','all_blocks_device.cc'),('reserve_restore_device','reserve_restore_device.cc')]:
        command=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX',
            '-I.','-Iinclude','-Iplugin/zenfs/fs',str(target/source),str(combined),'librocksdb_debug.a',
            '-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt','-o',str(target/name)]
        with (target/(name+'.编译.log')).open('w') as out:
            subprocess.run(command,cwd=root,stdout=out,stderr=subprocess.STDOUT,check=True)
    mapping=(target/'链接映射.txt').read_text().split('Discarded input sections',1)[0]
    members=re.findall(r'librocksdb_debug\.a\(([^)]+)\)',mapping)
    assert not {Path(x).name for x in members}&{x.name for x in objects},'误抽取旧ZenFS实现'
    assert helper.digest(library)==before,'改变了原静态库'
    print('全部ZenFS实现、边界及原始设备回归编译链接通过，原库未修改',flush=True)


if __name__=='__main__':main()
