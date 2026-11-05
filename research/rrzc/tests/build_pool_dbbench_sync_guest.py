"""只重编冻结协同副本的空间交接两端；其余对象和请求客户端逐字复用。"""
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
from build_all_blocks_guest import ROOT,digest
from pool_dbbench_sync_source import patched,require_ready


def restore(target):
    plan=json.loads((target/'同步重编清单.json').read_text())
    payload=target/'同步重编对象.tar.gz'
    assert digest(payload)==plan['压缩包SHA256']
    with tarfile.open(payload) as tar:
        members=tar.getmembers()
        assert len(members)==len(plan['文件']) and {m.name for m in members}==set(plan['文件'])
        for m in members:
            path=Path(m.name)
            assert m.isfile() and len(path.parts)==2 and path.parts[0] in ('复用ZenFS对象','复用客户端对象')
            assert path.suffix=='.o' and '..' not in path.parts
            out=target/path;out.parent.mkdir(exist_ok=True)
            with tar.extractfile(m) as source,out.open('xb') as dest:shutil.copyfileobj(source,dest)
            assert digest(out)==plan['文件'][m.name]
    return plan


def build(target,fs,obj):
    plan=restore(target);before=digest(ROOT/'librocksdb_debug.a')
    assert before==plan['原库SHA256'] and digest(obj)==plan['原对象SHA256']
    original={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    a,b=patched((fs/'fs_zenfs.cc').read_text(),(fs/'zbd_zenfs.cc').read_text())
    (fs/'fs_zenfs.cc').write_text(a);(fs/'zbd_zenfs.cc').write_text(b)
    shutil.copyfile(target/'rrzc_boundary_sync.h',fs/'rrzc_boundary_sync.h')
    require_ready(a,b)
    for name,sha in original.items():
        if name not in ('fs_zenfs.cc','zbd_zenfs.cc'):assert digest(fs/name)==sha,'无关源码被改：'+name
    directory=target/'复用ZenFS对象'
    for name in ('fs_zenfs','zbd_zenfs'):
        relative='plugin/zenfs/fs/'+name+'.cc'
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=ROOT,text=True,stderr=subprocess.STDOUT)
        matches=[]
        for line in dry.replace('\\\n',' ').splitlines():
            match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
            if match:
                args=shlex.split(match[1])
                if '-c' in args and relative in args:matches.append(args)
        assert len(matches)==1 and not any(s in matches[0] for s in (';','&&','|','>'))
        args=matches[0];args[args.index(relative)]=str(fs/(name+'.cc'))
        args[1:1]=['-I'+str(fs.parents[2]),'-I'+str(fs),'-I'+str(target)]
        args[args.index('-o')+1]=str(directory/(name+'.o'));args+=['-g0']
        (target/(name+'.同步编译命令.json')).write_text(json.dumps(args,ensure_ascii=False))
        with (target/(name+'.同步编译.log')).open('w') as log:
            subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    objects=sorted(directory.glob('*.o'))
    assert {p.name for p in objects}==set(plan['ZenFS对象'])
    for p in objects:
        if p.stem not in ('fs_zenfs','zbd_zenfs'):
            assert digest(p)==plan['文件']['复用ZenFS对象/'+p.name]
    combined=target/'fs_colocation.sync.o'
    subprocess.run(['ld','-r','-o',str(combined),*map(str,objects)],check=True)
    # 字符串来自实际新对象，防止仅源码有协议而链接仍用旧对象。
    symbols=subprocess.check_output(['nm','-C',str(combined)],text=True)
    assert 'rrzc_boundary::State()::state' in symbols
    raw=combined.read_bytes()
    for marker in ('受控空间请求：','受控空间维护完成：','受控空间确认：'):
        assert marker.encode() in raw
    combined.replace(obj)
    assert digest(ROOT/'librocksdb_debug.a')==before
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(target),
        str(target/'test_boundary_sync.cc'),'-o',str(target/'同步协议线程自检')],check=True)
    subprocess.run([str(target/'同步协议线程自检')],check=True,timeout=10)
    proof=dict(通过=True,仅重编=['fs_zenfs.cc','zbd_zenfs.cc'],原对象SHA256=plan['原对象SHA256'],
        新对象SHA256=digest(obj),协议头SHA256=digest(fs/'rrzc_boundary_sync.h'),
        其余源码未变=True,其余对象逐字复用=True,原库SHA256=before,
        模型选择正文未变=True,真实线程协议自检通过=True,
        边界='仅受控批次模式；新对象和原请求客户端重新链接，不能冒称旧二进制重复')
    (target/'同步接线构建核验.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return plan
