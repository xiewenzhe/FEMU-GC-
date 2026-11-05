"""独立目录编译完整ZenFS对象，覆盖新成员布局；原源码、原静态库不改。"""
from pathlib import Path
import json
import re
import shlex
import shutil
import subprocess
import sys
from build_all_blocks_guest import ROOT,digest
import colocation_source


def build(target):
    expected=json.loads((target/'expected.json').read_text());library=ROOT/'librocksdb_debug.a'
    before=digest(library)
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'原软件身份变化：'+name
    overlay=target/'协同池副本';fs=overlay/'plugin/zenfs/fs'
    shutil.copytree(ROOT/'plugin/zenfs/fs',fs,ignore=shutil.ignore_patterns('*.o','*.d','*.a','*.so'))
    transforms={'zbd_zenfs.h':colocation_source.zbd_header,'zbd_zenfs.cc':colocation_source.zbd_source,
        'io_zenfs.h':colocation_source.io_header,'io_zenfs.cc':colocation_source.io_source,
        'fs_zenfs.cc':colocation_source.fs_source}
    for name,transform in transforms.items():(fs/name).write_text(transform((fs/name).read_text()))
    # 三情形入口显式启用同一受控检查门；资格和victim选择正文不改。
    credit=(target/'启用协同池抵扣校准').is_file()
    choice=(target/'启用协同池四组合').is_file() or credit
    cases=(target/'启用协同池三情形').is_file() or choice
    if cases:
        from controlled_gc_source import patched
        path=fs/'fs_zenfs.cc';path.write_text(patched(path.read_text()))
    if choice:
        import colocation_choice_source
        path=fs/'fs_zenfs.cc';path.write_text(colocation_choice_source.frontend(path.read_text()))
        path=fs/'rrzc_direct_zenfs.inc';path.write_text(colocation_choice_source.selection(path.read_text()))
        shutil.copyfile(target/'rrzc_unit_cost_selector.h',fs/'rrzc_unit_cost_selector.h')
    cover=(target/'启用同空间目标接线').is_file()
    if cover:
        assert choice,'同空间目标接线要求共同观测选区对象'
        import maintenance_cover_source
        path=fs/'fs_zenfs.cc';path.write_text(maintenance_cover_source.frontend(path.read_text()))
        path=fs/'rrzc_direct_zenfs.inc';path.write_text(maintenance_cover_source.selection(path.read_text()))
        shutil.copyfile(target/'rrzc_maintenance_cover.h',fs/'rrzc_maintenance_cover.h')
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc'):
        shutil.copyfile(target/name,fs/name)
    directory=target/'协同池对象';directory.mkdir();objects=[]
    members=set(subprocess.check_output(['ar','t',str(library)],text=True).splitlines())
    for source in sorted(fs.glob('*.cc')):
        if source.stem+'.o' not in members:continue
        relative='plugin/zenfs/fs/'+source.name;out_name=relative[:-3]+'.o'
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,out_name],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        commands=[]
        for line in dry.replace('\\\n',' ').splitlines():
            match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if not match:continue
            command=shlex.split(match[1])
            if '-c' in command and relative in command:commands.append(command)
        assert len(commands)==1,'不能确定原生编译参数：'+source.name
        command=commands[0];assert not any(x in command for x in (';','&&','|','>'))
        command[command.index(relative)]=str(source)
        command[1:1]=['-I'+str(overlay),'-I'+str(fs)]
        obj=directory/(source.stem+'.o');command[command.index('-o')+1]=str(obj);command+=['-g0']
        (directory/(source.stem+'.command.json')).write_text(json.dumps(command,ensure_ascii=False))
        with (directory/(source.stem+'.log')).open('w') as log:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        objects.append(obj)
    assert {'fs_zenfs.o','io_zenfs.o','zbd_zenfs.o'}<={p.name for p in objects}
    combined=target/'fs_colocation.o'
    subprocess.run(['ld','-r','-o',str(combined),*map(str,objects)],check=True)
    for mode,obj,includes in (('reference',target/'fs_all_blocks.o',[]),
                             ('prototype',combined,['-DRRZC_COLOCATION_ADAPTER','-I'+str(overlay)])):
        mapping=target/(mode+'.map')
        client='colocation_credit' if credit else 'colocation_choice' if choice else 'colocation_cases'
        command=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
            '-DROCKSDB_PLATFORM_POSIX',*includes,'-I.','-Iinclude','-Iplugin/zenfs/fs',
            str(target/'colocation_device.cc'),str(obj),'librocksdb_debug.a','-Wl,--strip-debug',
            '-Wl,-Map,'+str(mapping),'-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
            '-o',str(target/('colocation_'+mode))]
        with (target/(mode+'.build.log')).open('w') as log:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        used=mapping.read_text().split('Discarded input sections',1)[0]
        assert not any('librocksdb_debug.a('+p.name+')' in used for p in objects),'误抽取旧布局ZenFS对象'
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'编译修改原源码：'+name
    assert digest(library)==before,'原静态库被修改'
    (target/'协同池构建身份.json').write_text(json.dumps(dict(通过=True,原库SHA256=before,
        对象SHA256=digest(combined),原程序SHA256=digest(target/'colocation_reference'),
        新程序SHA256=digest(target/'colocation_prototype'),
        边界='独立完整ZenFS对象及临时文件系统；K=1元数据标签10不能由旧版本读取；不覆盖旧库'),
        ensure_ascii=False,indent=2)+'\n')
    for client in (('colocation_choice','colocation_credit') if cover else (client,) if cases else ()):
        prefix=client if cover else 'cases'
        mapping=target/(prefix+'.map')
        command=['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX',
            '-DROCKSDB_PLATFORM_POSIX','-DRRZC_COLOCATION_ADAPTER','-I'+str(overlay),'-I'+str(fs),
            '-I'+str(target),'-I.','-Iinclude','-Iplugin/zenfs/fs',str(target/(client+'_device.cc')),
            str(combined),'librocksdb_debug.a','-Wl,--strip-debug','-Wl,-Map,'+str(mapping),
            '-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
            '-o',str(target/client)]
        # 四组合客户端逐字节校验约6.6GiB；优化本客户端的校验循环，原ZenFS编译参数不改。
        if choice:command.insert(1,'-O2')
        with (target/(prefix+'.build.log')).open('w') as log:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        used=mapping.read_text().split('Discarded input sections',1)[0]
        assert not any('librocksdb_debug.a('+p.name+')' in used for p in objects)
        assert digest(library)==before,'新增载荷链接修改原库'
        identity=('构建身份-'+client+'.json') if cover else '三情形构建身份.json'
        (target/identity).write_text(json.dumps(dict(通过=True,
            客户端SHA256=digest(target/client),对象SHA256=digest(combined),
            原库SHA256=before,受控GC检查门=True,K0与K1使用同一程序=client!='colocation_credit',
            抵扣校准=credit,Greedy与模型使用同一程序=choice,
            客户端源码SHA256=digest(target/(client+'_device.cc')),
            同空间目标接线=cover,
            四组合=choice,候选边界='original' if choice else '原Greedy'),
            ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':build(Path(sys.argv[1]))
