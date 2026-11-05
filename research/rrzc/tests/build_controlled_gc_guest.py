#!/usr/bin/env python3
"""只为独立受控实验重编ZenFS对象；原源码与静态库保持原样。"""
from pathlib import Path
import json
import re
import shlex
import subprocess
import sys
import build_all_blocks_guest as helper
from controlled_gc_source import patched


def build(target):
    root=helper.ROOT;library=root/'librocksdb_debug.a';before=helper.digest(library)
    expected=json.loads((target/'expected.json').read_text())
    for name,value in expected.items():
        assert helper.digest(root/name)==value,'受控编译前原软件身份变化：'+name
    source=root/'plugin/zenfs/fs/fs_zenfs.cc'
    altered=target/'受控GC-fs_zenfs.cc';altered.write_text(patched(source.read_text()))
    directory=target/'受控ZenFS对象';directory.mkdir();objects=[]
    members={Path(x).name for x in subprocess.check_output(['ar','t',str(library)],text=True).splitlines()}
    for native in sorted((root/'plugin/zenfs/fs').glob('*.cc')):
        if native.stem+'.o' not in members:continue
        relative=str(native.relative_to(root));object_name=relative[:-3]+'.o'
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,object_name],cwd=root,text=True,stderr=subprocess.STDOUT)
        choices=[]
        for line in dry.replace('\\\n',' ').splitlines():
            match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if not match:continue
            command=shlex.split(match[1])
            if '-c' in command and relative in command:choices.append(command)
        assert len(choices)==1,'原生ZenFS编译命令不唯一：'+relative
        command=choices[0];assert not any(x in command for x in ('&&',';','|','>'))
        if native==source:
            command[command.index(relative)]=str(altered)
            command+=['-Iplugin/zenfs/fs']
        obj=directory/(native.stem+'.o');command[command.index('-o')+1]=str(obj);command+=['-g0']
        with (directory/(native.stem+'.log')).open('w') as output:
            subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT,check=True)
        objects.append(obj)
    assert {'fs_zenfs.o','io_zenfs.o','zbd_zenfs.o'}<={x.name for x in objects},'核心ZenFS对象缺失'
    subprocess.run(['ld','-r','-o',str(target/'fs_all_blocks.o'),*map(str,objects)],check=True)
    for name,value in expected.items():
        assert helper.digest(root/name)==value,'受控编译修改原软件：'+name
    assert helper.digest(library)==before,'受控编译修改静态库'
    (target/'受控GC对象身份.json').write_text(json.dumps(dict(通过=True,
        原ZenFS及库保持不变=True,受控源码SHA256=helper.digest(altered),
        受控对象SHA256=helper.digest(target/'fs_all_blocks.o')),
        ensure_ascii=False,indent=2)+'\n')
    print('受控GC检查门对象已独立编译，原ZenFS及库身份不变',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
