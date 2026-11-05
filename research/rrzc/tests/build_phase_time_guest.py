"""在独立源码副本重编计时程序；原库、选择器和请求计划保持原字节。"""
import json
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
from build_all_blocks_guest import ROOT, digest
from build_all_blocks_dbbench_guest import commands
import phase_time_source


def command(dry,relative):
    # 独立构建器不导入旧完整构建链，避免引入未部署的无关模块。
    choices=[]
    for line in dry.replace('\\\n',' ').splitlines():
        match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
        if not match:continue
        argv=shlex.split(match.group(1))
        if '-c' in argv and relative in argv:choices.append(argv)
    assert len(choices)==1 and not any(s in choices[0] for s in (';','&&','||','|','>'))
    return choices[0]


def build(target,transforms=phase_time_source,output_name='区间计时构建.json',smoke_overlap=False):
    original = json.loads((target/'生命周期构建核验.json').read_text())
    headers=json.loads((target/'原客户端头文件身份.json').read_text())
    assert all(digest(target/n)==sha for n,sha in headers.items()),'客户端来源头文件身份不同'
    expected = json.loads((target/'计时支持对象.json').read_text())
    with tarfile.open(target/'计时支持对象.tar.gz') as tar:
        members = tar.getmembers()
        assert {m.name for m in members} == set(expected) and len(members) == len(expected)
        for m in members:
            assert m.isfile() and len(Path(m.name).parts) == 1
            dest = target/m.name
            assert not dest.exists()
            with tar.extractfile(m) as src, dest.open('xb') as out: shutil.copyfileobj(src,out)
            assert digest(dest) == expected[m.name]
    overlay = target/'协同池副本'; fs = overlay/'plugin/zenfs/fs'
    before = {p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    native = digest(ROOT/'librocksdb_debug.a')
    assert native == json.loads((target/'冻结对象清单.json').read_text())['原库SHA256']
    if hasattr(transforms,'fs_header'):
        p=fs/'fs_zenfs.h';p.write_text(transforms.fs_header(p.read_text()))
    if hasattr(transforms,'metrics'):
        p=fs/'metrics.h';p.write_text(transforms.metrics(p.read_text()))
    for name, transform in (('zbd_zenfs.cc',transforms.zbd),
                           ('io_zenfs.cc',transforms.io),('fs_zenfs.cc',transforms.fs)):
        p = fs/name; p.write_text(transform(p.read_text()))
    if hasattr(transforms,'zbdlib'):
        p=fs/'zbdlib_zenfs.cc';p.write_text(transforms.zbdlib(p.read_text()))
        shutil.copyfile(target/'rrzc_read_cost.h',fs/'rrzc_read_cost.h')
    shutil.copyfile(target/'rrzc_phase_time.h',fs/'rrzc_phase_time.h')
    source = target/'校准-db_bench_tool.cc'
    old_source = digest(source); source.write_text(transforms.client(source.read_text()))
    units = target/'计时对象'; units.mkdir()
    def run(argv,name):
        (target/(name+'.命令.json')).write_text(json.dumps(argv,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('w') as out:
            subprocess.run(argv,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)
    members = set(subprocess.check_output(['ar','t',str(ROOT/'librocksdb_debug.a')],text=True).splitlines())
    objects = []
    for p in sorted(fs.glob('*.cc')):
        if p.stem+'.o' not in members: continue
        relative = 'plugin/zenfs/fs/'+p.name
        dry = subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        argv = command(dry,relative); argv[argv.index(relative)] = str(p)
        obj = units/(p.stem+'.o'); argv[argv.index('-o')+1] = str(obj)
        argv[1:1] = ['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)]; argv += ['-g0']
        run(argv,'计时编译-'+p.stem); objects.append(obj)
    assert len(objects) == 5
    run(['ld','-r','-o',str(target/'计时ZenFS.o'),*map(str,objects)],'计时对象合并')
    dry = subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
        'ROCKSDB_PLUGINS=zenfs','-W','tools/db_bench_tool.cc','db_bench'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
    cc, link = commands(dry); old_obj = cc[cc.index('-o')+1]
    cc[cc.index('tools/db_bench_tool.cc')] = str(source)
    cc[cc.index('-o')+1] = str(target/'计时客户端.o')
    cc[1:1] = ['-I'+str(overlay),'-I'+str(fs),'-I'+str(target)]; cc += ['-g0']
    run(cc,'计时客户端编译')
    link = [str(target/'计时客户端.o') if x == old_obj else 'librocksdb_debug.a' if x == '-lrocksdb_debug' else x for x in link]
    at = link.index('librocksdb_debug.a')
    link[at:at] = [str(target/'计时ZenFS.o'),*[str(target/n) for n in expected]]
    link[link.index('-o')+1] = str(target/'计时-db_bench')
    mapping = target/'计时链接映射.txt'; link += ['-Wl,--strip-debug','-Wl,-Map,'+str(mapping)]
    run(link,'计时客户端链接')
    used = mapping.read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in used for n in ('fs_zenfs.o','zbd_zenfs.o','io_zenfs.o','flush_job.o'))
    run(['env','RRZC_PHASE_TIME='+str(target/'计时小测区间.bin')]+
        (['RRZC_READ_COST='+str(target/'读成本小测.bin')] if hasattr(transforms,'zbdlib') else [])+
        (['RRZC_END_TO_END='+str(target/'应用小测完成.bin')] if getattr(transforms,'end_to_end',False) else [])+[
        'RRZC_SST_LIFETIME_SMOKE=1','RRZC_SST_LIFETIME_PERSISTENT_H=1',
        'RRZC_SST_LIFETIME_INTERLEAVED=1','RRZC_CAL_TLS_SEED=1',
        'RRZC_CAL_SINGLE_BACKGROUND='+str(int(not smoke_overlap)),
        'RRZC_CAL_REQUEST_TRACE='+str(target/'计时小测请求.bin'),str(target/'计时-db_bench'),
        '--db='+str(target/'计时小测库'),'--benchmarks=fillseq,rrzcsstlifetime,readseq,verifychecksum',
        '--num=1024','--value_size=4096','--compression_type=none','--disable_wal=1',
        '--cache_size=0','--row_cache_size=0','--threads=1','--seed=20261023','--reads=1024','--writes=480']+
        (['--num_high_pri_threads=1','--num_low_pri_threads=1','--max_background_jobs=2',
        '--max_background_flushes=1','--max_background_compactions=1'] if smoke_overlap else []), '计时客户端小测')
    raw = (target/'计时小测请求.bin').read_bytes(); assert raw[:8] == b'RRZCREQ1'
    rows = list(struct.iter_unpack('<7Q',raw[8:])); wanted = []
    for part in range(2):
        reads = 0
        for n in range(240):
            wanted.append((1,16+n))
            while reads < (n+1)*128//240:
                wanted.append((0,(part*128+reads)%16)); reads += 1
    assert len(rows) == len(wanted) == 736
    counts = [0,0]; last = 0
    for i,(r,(role,key)) in enumerate(zip(rows,wanted)):
        assert r[:4] == (i,role,counts[role],key) and last <= r[4] <= r[5] <= r[6]
        counts[role] += 1; last = r[6]
    raw = (target/'计时小测区间.bin').read_bytes()
    assert raw[:16] == b'RRZCTM01'+bytes(8) and (len(raw)-16)%48 == 0
    records = list(struct.iter_unpack('<6Q',raw[16:]))
    assert {r[0] for r in records} == ({1,2,4} if smoke_overlap else {1,2,3,4}) and all(r[1] <= r[2] for r in records)
    allowed={'zbd_zenfs.cc','io_zenfs.cc','fs_zenfs.cc'}
    allowed.update(getattr(transforms,'allowed_extra',set()))
    if hasattr(transforms,'fs_header'):allowed.add('fs_zenfs.h')
    assert all(digest(fs/n) == sha for n,sha in before.items() if n not in allowed)
    assert digest(ROOT/'librocksdb_debug.a') == native
    assert all(digest(target/n) == sha for n,sha in expected.items())
    if hasattr(transforms,'zbdlib'):
        raw=(target/'读成本小测.bin').read_bytes()
        assert raw[:16]==b'RRZCRD01'+bytes(8) and (len(raw)-16)%80==0
        costs=list(struct.iter_unpack('<10Q',raw[16:]))
        assert sum(r[0]==1 for r in costs)==256 and sum(r[0]==4 for r in costs)==4
    if getattr(transforms,'end_to_end',False):
        raw=(target/'应用小测完成.bin').read_bytes()
        assert raw[:8]==b'RRZCE201' and struct.unpack('<2Q',raw[8:24])==(64,32) and (len(raw)-24)%80==0
        done=list(struct.iter_unpack('<10Q',raw[24:]))
        assert sorted(r[0] for r in done)==list(range(736))
        assert sum(r[1] for r in done)==480
        assert all(list(r[3:9])==sorted(r[3:9]) and bool(r[9])==bool(r[1]) for r in done)
    (target/'db_bench').rename(target/'计时前-db_bench')
    (target/'计时-db_bench').rename(target/'db_bench')
    proof = dict(通过=True,五单元编译=True,原736请求逐条通过=True,区间小测通过=True,
        原程序SHA256=original['程序SHA256'],程序SHA256=digest(target/'db_bench'),
        原请求源码SHA256=old_source,请求源码SHA256=digest(source),
        ZenFS对象SHA256=digest(target/'计时ZenFS.o'),原库SHA256=native,
        支持对象=expected,客户端头文件=headers,计时头SHA256=digest(fs/'rrzc_phase_time.h'),
        不变源码={n:sha for n,sha in before.items() if n not in allowed},
        边界='只增加内存区间观测；请求逻辑、模型、分配与维护正文不改；不能与旧观测计时直接比较')
    if smoke_overlap:
        proof.update(前后台重叠=True,原业务排空=False,原请求顺序保持=True,
            边界='单前台固定输入、异步Flush、后台独立运行、批次GC门控关闭；只在收尾排空；五单元同ABI编译')
    (target/output_name).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print('区间计时五单元编译、736请求和内容校验通过；原库及支持对象不变',flush=True)


if __name__ == '__main__': build(Path(sys.argv[1]))
