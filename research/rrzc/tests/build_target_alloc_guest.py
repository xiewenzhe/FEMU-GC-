#!/usr/bin/env python3
"""重编独立ZenFS组合对象，只为zbd实现增加默认关闭的目标采证；原库/源码不改。"""
from pathlib import Path
import json
import re
import shlex
import subprocess
from build_all_blocks_guest import ROOT,digest
from target_alloc_source import patched,controlled_retry,synchronous_space_retry


def build(target):
    expected=json.loads((target/'expected.json').read_text())
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'构建前身份变化：'+name
    original=ROOT/'plugin/zenfs/fs/zbd_zenfs.cc'
    boundary=(target/'启用受控GC检查').exists()
    sync_space=(target/'启用受控空间交接').exists()
    matched=(target/'启用共同观测').exists()
    changed=target/'目标采证-zbd_zenfs.cc';altered=patched(original.read_text())
    if boundary:altered=controlled_retry(altered)
    if sync_space:altered=synchronous_space_retry(altered)
    changed.write_text(altered)
    (target/'原始-zbd_zenfs.cc').write_bytes(original.read_bytes())
    controlled=None
    if boundary:
        from controlled_gc_source import patched as boundary_patch,synchronous_space
        controlled=target/'边界受控-fs_zenfs.cc'
        gc_text=boundary_patch((ROOT/'plugin/zenfs/fs/fs_zenfs.cc').read_text())
        if sync_space:gc_text=synchronous_space(gc_text)
        controlled.write_text(gc_text)
    instrumented_backend=None
    if matched:
        from matched_observation_source import direct,backend
        inc=ROOT/'plugin/zenfs/fs/rrzc_direct_zenfs.inc'
        (target/'共同观测-原始选区.inc').write_bytes(inc.read_bytes())
        (target/'rrzc_direct_zenfs.inc').write_text(direct(inc.read_text()))
        native_backend=ROOT/'plugin/zenfs/fs/zbdlib_zenfs.cc'
        instrumented_backend=target/'设备摘要-zbdlib_zenfs.cc'
        instrumented_backend.write_text(backend(native_backend.read_text()))
    unit_cost=(target/'启用单位回收成本').is_file()
    reclaim_price=(target/'启用RR空间替代成本').is_file()
    assert not reclaim_price or unit_cost,'空间定价必须显式使用独立单位回收模型'
    if unit_cost:
        assert matched and boundary and controlled is not None,'单位回收模型要求共同观测及受控对象'
        from unit_cost_source import direct as unit_direct,frontend as unit_frontend
        inc=target/'rrzc_direct_zenfs.inc'
        inc.write_text(unit_direct(inc.read_text(),reclaim_price))
        controlled.write_text(unit_frontend(controlled.read_text(),reclaim_price))
    directory=target/'目标采证对象';directory.mkdir();objects=[]
    members={Path(x).name for x in subprocess.check_output(['ar','t',str(ROOT/'librocksdb_debug.a')],text=True).splitlines()}
    for source in sorted((ROOT/'plugin/zenfs/fs').glob('*.cc')):
        if source.stem+'.o' not in members:continue
        relative=str(source.relative_to(ROOT));obj=directory/(source.stem+'.o')
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        matches=[]
        for line in dry.replace('\\\n',' ').splitlines():
            match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if match:
                words=shlex.split(match[1])
                if '-c' in words and relative in words:matches.append(words)
        assert len(matches)==1 and not any(x in matches[0] for x in (';','&&','|','>')),'原生命令不唯一'
        argv=matches[0];argv[argv.index('-o')+1]=str(obj);argv+=['-g0']
        if source==original:
            argv[argv.index(relative)]=str(changed);argv+=['-Iplugin/zenfs/fs','-I'+str(target)]
        elif boundary and source.name=='fs_zenfs.cc':
            argv[argv.index(relative)]=str(controlled);argv+=['-Iplugin/zenfs/fs','-I'+str(target)]
        elif matched and source.name=='zbdlib_zenfs.cc':
            argv[argv.index(relative)]=str(instrumented_backend);argv+=['-Iplugin/zenfs/fs','-I'+str(target)]
        with (directory/(source.stem+'.log')).open('w') as f:
            subprocess.run(argv,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
        objects.append(obj)
    assert {'fs_zenfs.o','io_zenfs.o','zbd_zenfs.o'}<={o.name for o in objects},'核心对象缺失'
    subprocess.run(['ld','-r','-o',str(target/'fs_all_blocks.o'),*map(str,objects)],check=True)
    for name,sha in expected.items():assert digest(ROOT/name)==sha,'构建改变原软件：'+name
    (target/'目标采证身份.json').write_text(json.dumps(dict(原软件保持不变=True,
        原源码SHA256=digest(original),诊断源码SHA256=digest(changed),
        组合对象SHA256=digest(target/'fs_all_blocks.o'),
        受控批次检查=boundary,受控GC源码SHA256=digest(controlled) if boundary else None,
        受控分配重试串行=boundary,
        受控空间请求确认=sync_space,
        共同观测=matched,
        单位回收模型=unit_cost,
        RR空间替代计价=reclaim_price,
        选区片段SHA256=digest(target/'rrzc_direct_zenfs.inc') if matched else None,
        设备读摘要源码SHA256=digest(instrumented_backend) if matched else None,
        边界='新诊断二进制，不能与旧二进制混合为同构建重复；未修改筛选和Acquire次数'),ensure_ascii=False,indent=2)+'\n')
