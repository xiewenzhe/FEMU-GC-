"""增加只读日志观测方法并同ABI重编，GC/RR正文及支持对象不变。"""
import json
from pathlib import Path
import shutil
import re
import shlex
import struct
import subprocess
import sys
from build_all_blocks_guest import ROOT,digest
from build_all_blocks_dbbench_guest import commands
from pool_tail_stream_source import client,fs_header,fs_source


def build(target):
    identity=json.loads((target/'生命周期构建核验.json').read_text())
    plan=json.loads((target/'冻结对象清单.json').read_text())
    expected=json.loads((target/'持续负载支持对象.json').read_text())
    obj=target/'fs_colocation.o';overlay=target/'协同池副本';fs=overlay/'plugin/zenfs/fs'
    assert digest(obj)==plan['对象SHA256']==identity['冻结ZenFS对象SHA256']
    assert all(digest(target/n)==sha for n,sha in expected.items())
    old=digest(target/'db_bench');assert old==identity['程序SHA256']
    native=digest(ROOT/'librocksdb_debug.a');assert native==plan['原库SHA256']
    original_obj=digest(obj);shutil.copyfile(obj,target/'持续前-ZenFS对象.o')
    before={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    for n,fn in [('fs_zenfs.h',fs_header),('fs_zenfs.cc',fs_source)]:
        p=fs/n;p.write_text(fn(p.read_text()))
    build_dir=target/'持续观测构建';build_dir.mkdir();units=[]
    members=set(subprocess.check_output(['ar','t',str(ROOT/'librocksdb_debug.a')],text=True).splitlines())
    for p in sorted(fs.glob('*.cc')):
        if p.stem+'.o' not in members:continue
        relative='plugin/zenfs/fs/'+p.name
        dry_unit=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static',
            'USE_RTTI=0','ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        choices=[]
        for line in dry_unit.replace('\\\n',' ').splitlines():
            m=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if m:
                argv=shlex.split(m[1])
                if '-c' in argv and relative in argv:choices.append(argv)
        assert len(choices)==1
        argv=choices[0];assert not any(s in argv for s in (';','&&','||','|','>'))
        argv[argv.index(relative)]=str(p);dest=build_dir/(p.stem+'.o')
        argv[argv.index('-o')+1]=str(dest);argv[1:1]=['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)]
        argv+=['-g0']
        with (target/('持续观测-'+p.stem+'.log')).open('w') as log:
            subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        units.append(dest)
    assert len(units)==5
    for n,sha in before.items():
        if n not in ('fs_zenfs.h','fs_zenfs.cc'):assert digest(fs/n)==sha,'无关ZenFS源码改变：'+n
    subprocess.run(['ld','-r','-o',str(obj),*map(str,units)],check=True)
    observed_obj=digest(obj)
    source=target/'校准-db_bench_tool.cc';original=source.read_text()
    shutil.copyfile(source,target/'持续前客户端.cc');source.write_text(client(original))
    dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    compile_args,link_args=commands(dry);old_obj=compile_args[compile_args.index('-o')+1]
    compile_args[compile_args.index('tools/db_bench_tool.cc')]=str(source)
    compile_args[compile_args.index('-o')+1]=str(target/'持续客户端.o')
    compile_args[1:1]=['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)];compile_args+=['-g0']
    def run(args,name):
        (target/(name+'.命令.json')).write_text(json.dumps(args,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('w') as out:
            subprocess.run(args,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)
    run(compile_args,'持续客户端编译')
    link_args=[str(target/'持续客户端.o') if x==old_obj else 'librocksdb_debug.a' if x=='-lrocksdb_debug' else x for x in link_args]
    at=link_args.index('librocksdb_debug.a');link_args[at:at]=[str(obj),*[str(target/n) for n in expected]]
    link_args[link_args.index('-o')+1]=str(target/'持续-db_bench')
    mapping=target/'持续客户端链接映射.txt';link_args+=['-Wl,--strip-debug','-Wl,-Map,'+str(mapping)]
    run(link_args,'持续客户端链接')
    used=mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in (*plan['禁止旧对象'],'flush_job.o'))
    assert digest(obj)==observed_obj and digest(target/'持续前-ZenFS对象.o')==original_obj
    assert digest(ROOT/'librocksdb_debug.a')==native
    assert digest(target/'db_bench')==old and all(digest(target/n)==sha for n,sha in expected.items())
    # 一次普通目录检查保留原736条交错输入；不声称它覆盖真实阶段观测。
    cmd=['env','RRZC_SST_LIFETIME_SMOKE=1','RRZC_SST_LIFETIME_PERSISTENT_H=1',
        'RRZC_SST_LIFETIME_INTERLEAVED=1','RRZC_CAL_TLS_SEED=1','RRZC_CAL_SINGLE_BACKGROUND=1',
        'RRZC_CAL_REQUEST_TRACE='+str(target/'持续小测请求.bin'),str(target/'持续-db_bench'),
        '--db='+str(target/'持续小测库'),'--benchmarks=fillseq,rrzcsstlifetime,readseq,verifychecksum',
        '--num=1024','--value_size=4096','--compression_type=none','--disable_wal=1',
        '--cache_size=0','--row_cache_size=0','--threads=1','--seed=20261023','--reads=1024','--writes=480']
    run(cmd,'持续客户端小测')
    raw=(target/'持续小测请求.bin').read_bytes();assert raw[:8]==b'RRZCREQ1'
    rows=list(struct.iter_unpack('<7Q',raw[8:]));wanted=[]
    for part in range(2):
        reads=0
        for n in range(240):
            wanted.append((1,16+n))
            while reads<(n+1)*128//240:wanted.append((0,(part*128+reads)%16));reads+=1
    counts=[0,0];last=0
    assert len(rows)==len(wanted)==736
    for i,(row,(role,key)) in enumerate(zip(rows,wanted)):
        assert row[:4]==(i,role,counts[role],key) and last<=row[4]<=row[5]<=row[6]
        counts[role]+=1;last=row[6]
    (target/'db_bench').rename(target/'持续前-db_bench');(target/'持续-db_bench').rename(target/'db_bench')
    stamp=dict(通过=True,原程序SHA256=old,程序SHA256=digest(target/'db_bench'),
        客户端源码SHA256=digest(source),ZenFS对象SHA256=digest(obj),原库SHA256=native,
        原ZenFS对象SHA256=original_obj,支持对象=expected,原736请求小测通过=True,
        窗口模式=[12,24],阶段间不重挂载=True,
        观测源码={n:digest(fs/n) for n in ('fs_zenfs.h','fs_zenfs.cc')},
        说明='仅新增Logger Flush/字节长度/阶段标记方法并同ABI重编五单元；原GC/RR/分配正文未改')
    (target/'持续负载构建.json').write_text(json.dumps(stamp,ensure_ascii=False,indent=2)+'\n')
    print('持续客户端和只读日志观测编译通过，原736请求通过，维护正文和支持对象未改',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]))
