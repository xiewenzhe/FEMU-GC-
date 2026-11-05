"""在独立目录重编所有ZenFS单元，接真实Flush来源；原库及请求体保持原字节。"""
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT,digest
from build_sst_interleaved_guest import build as build_client
import flush_origin_source


def command(dry,relative):
    rows=[]
    for line in dry.replace('\\\n',' ').splitlines():
        m=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
        if not m:continue
        argv=shlex.split(m[1])
        if '-c' in argv and relative in argv:rows.append(argv)
    assert len(rows)==1 and not any(s in rows[0] for s in (';','&&','||','|','>'))
    return rows[0]


def build(target):
    plan=json.loads((target/'冻结对象清单.json').read_text())
    original_archive=target/'冻结对象.tar.gz'
    assert digest(original_archive)==plan['压缩包SHA256']
    stage=target/'Flush来源构建';stage.mkdir();original_sources={}
    with tarfile.open(original_archive) as tar:
        members=tar.getmembers();assert len(members)==len(plan['文件']) and {m.name for m in members}==set(plan['文件'])
        for m in members:
            p=Path(m.name);assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert m.name=='fs_colocation.o' or p.parts[:4]==('协同池副本','plugin','zenfs','fs')
            dest=stage/p;dest.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as source,dest.open('xb') as out:shutil.copyfileobj(source,out)
            assert digest(dest)==plan['文件'][m.name];original_sources[m.name]=digest(dest)
    fs=stage/'协同池副本/plugin/zenfs/fs'
    for name,transform in (('io_zenfs.h',flush_origin_source.header),('io_zenfs.cc',flush_origin_source.io),
                           ('fs_zenfs.cc',flush_origin_source.fs)):
        p=fs/name;p.write_text(transform(p.read_text()))
    shutil.copyfile(target/'rrzc_flush_origin.h',fs/'rrzc_flush_origin.h')
    pair=(target/'启用配对准入构建').is_file()
    pair_changed=set()
    stream=(target/'启用在线协同机制构建').is_file()
    if stream:
        assert not pair,'在线分类首版不混入旧配对拒绝和双目标支配门'
        import stream_profile_source
        for name,transform in (('zbd_zenfs.h',stream_profile_source.zbd_header),
                               ('io_zenfs.h',stream_profile_source.io_header),
                               ('io_zenfs.cc',stream_profile_source.io),
                               ('fs_zenfs.cc',stream_profile_source.fs),
                               ('rrzc_colocation_zenfs.inc',stream_profile_source.adapter)):
            p=fs/name;p.write_text(transform(p.read_text()));pair_changed.add(name)
        shutil.copyfile(target/'rrzc_stream_profile.h',fs/'rrzc_stream_profile.h')
    if pair:
        import pair_admission_source
        for name,transform in (('zbd_zenfs.h',pair_admission_source.zbd_header),
                ('io_zenfs.h',pair_admission_source.io_header),('io_zenfs.cc',pair_admission_source.io),
                ('fs_zenfs.cc',pair_admission_source.fs),('rrzc_colocation_zenfs.inc',pair_admission_source.adapter)):
            path=fs/name;path.write_text(transform(path.read_text()));pair_changed.add(name)
        for name in ('rrzc_pair_admission.h','rrzc_pair_zenfs.inc'):
            shutil.copyfile(target/name,fs/name)
    compare=(target/'启用双目标比较构建').is_file()
    if compare:
        assert pair,'双目标比较需要同版本的有效extent账目'
        import target_compare_source
        for name,transform in (('zbd_zenfs.h',target_compare_source.header),
                               ('rrzc_colocation_zenfs.inc',target_compare_source.adapter)):
            path=fs/name;path.write_text(transform(path.read_text()));pair_changed.add(name)
        for name in ('rrzc_target_compare.h','rrzc_target_compare_zenfs.inc'):
            shutil.copyfile(target/name,fs/name)
    diag=target/'诊断-flush_job.cc';shutil.copyfile(diag,target/'原诊断-flush_job.cc')
    diag.write_text(flush_origin_source.flush(diag.read_text()))
    units=stage/'对象';units.mkdir();objects=[]
    def run(argv,name):
        (target/(name+'.命令.json')).write_text(json.dumps(argv,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('w') as log:subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    members=set(subprocess.check_output(['ar','t',str(ROOT/'librocksdb_debug.a')],text=True).splitlines())
    for p in sorted(fs.glob('*.cc')):
        if p.stem+'.o' not in members:continue
        relative='plugin/zenfs/fs/'+p.name
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        argv=command(dry,relative);argv[argv.index(relative)]=str(p)
        dest=units/(p.stem+'.o');argv[argv.index('-o')+1]=str(dest)
        argv[1:1]=['-I'+str(fs.parents[2]),'-I'+str(fs),'-I'+str(target)];argv+=['-g0']
        run(argv,'来源编译-'+p.stem);objects.append(dest)
    assert len(objects)==5,'新文件成员要求所有五单元同ABI重编'
    obj=stage/'fs_colocation.o';run(['ld','-r','-o',str(obj),*map(str,objects)],'来源对象合并')
    for name,sha in original_sources.items():
        if Path(name).name not in {'fs_colocation.o','io_zenfs.h','io_zenfs.cc','fs_zenfs.cc'}|pair_changed:
            assert digest(stage/name)==sha,'无关依赖变化：'+name
    # 旧包与清单保留；交给既有客户端构建器的新包包含全部同ABI对象及对应源码。
    original_archive.rename(target/'原冻结对象.tar.gz')
    (target/'原冻结对象清单.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    files={'fs_colocation.o':obj}
    for p in (stage/'协同池副本').rglob('*'):
        if p.is_file():files[str(p.relative_to(stage))]=p
    with tarfile.open(original_archive,'x:gz') as tar:
        for name,p in files.items():tar.add(p,arcname=name,recursive=False)
    plan.update(对象SHA256=digest(obj),文件={n:digest(p) for n,p in files.items()},压缩包SHA256=digest(original_archive))
    (target/'冻结对象清单.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    build_client(target)
    # 元数据探针使用同一新类布局，和db_bench保持同ABI。
    overlay=target/'协同池副本';actual=overlay/'plugin/zenfs/fs'
    run(['g++','-std=c++20','-Wall','-Wextra','-Werror','-fno-rtti','-DOS_LINUX','-DROCKSDB_PLATFORM_POSIX',
        '-I'+str(overlay),'-I'+str(actual),'-I.','-Iinclude',str(target/'flush_metadata_probe.cc'),str(target/'fs_colocation.o'),
        'librocksdb_debug.a','-Wl,--strip-debug','-lzbd','-lsnappy','-llz4','-lzstd','-lz','-lbz2','-pthread','-ldl','-lrt',
        '-o',str(target/'flush_metadata_probe')],'来源元数据探针编译')
    identity=json.loads((target/'生命周期构建核验.json').read_text())
    expected=json.loads((target/'Flush来源构建计划.json').read_text())
    assert identity['新客户端源码SHA256']==expected['请求源码SHA256']
    assert digest(ROOT/'librocksdb_debug.a')==plan['原库SHA256']
    identity.update(ZenFS未重编=False,ZenFS完整五单元重编=True,元数据版本=11,
        模型及原库未修改=True,原冻结对象SHA256=expected['原对象SHA256'],
        边界='只增加真实Flush来源与标签持久化；请求源码未改，原GC/RR/选择器未改；新ABI全部对象重编')
    if pair:
        identity.update(配对准入版本=1,配对准入默认关闭=True,
            边界='增加共享目标准入及有效extent账目；两组同程序采证；请求、元数据格式、GC/RR/选择器不变')
    if compare:
        identity.update(双目标比较版本=1,双目标比较默认关闭=True,
            边界='只比较必要H迁移的真实目标，全部非H及剩余容量作保守支配判断；不是未来净收益预测；新文件、元数据及GC/RR选源不变')
    if stream:
        identity.update(在线分类版本=2,无手工H标签=True,
            边界='SST实际重复读取识别H，真实Flush来源代理W，K固定1；统计随进程重新学习，非精确寿命预测')
    (target/'生命周期构建核验.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':build(Path(sys.argv[1]))
