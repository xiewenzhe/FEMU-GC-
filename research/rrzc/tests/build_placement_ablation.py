"""本地生成隔离源码及可审查构建命令；不启动客机、不创建镜像、不运行业务。"""
import argparse
import hashlib
import json
from pathlib import Path
import difflib
import subprocess
import tarfile

import placement_ablation_source as change
import end_to_end_source as timing

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-end-to-end-v1-20261105-results')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()


def isolate_dependencies(argv,objects,name):
    # 移除原依赖文件位置和make目标，统一写到隔离目录，不能留下孤立路径参数。
    result=[];i=0
    while i<len(argv):
        arg=argv[i]
        if arg in ('-MF','-MT','-MQ'):
            assert i+1<len(argv), '原编译器依赖参数不完整'
            i+=2;continue
        if arg.startswith(('-MF','-MT','-MQ','-Wp,-M')):
            i+=1;continue
        result.append(arg);i+=1
    result+=['-MMD','-MF',str((objects/(name+'.d')).resolve()),
        '-MT',str((objects/(name+'.o')).resolve())]
    return result


def prepare(source,output):
    assert not output.exists(), '不覆盖已有独立副本'
    proof=json.loads((source/'前后台重叠构建.json').read_text())
    archive=source/'源码/冻结对象.tar.gz'
    materials=json.loads((source/'源码/冻结对象清单.json').read_text())
    assert digest(archive)==materials['压缩包SHA256'], '冻结源码归档身份不同'
    state=json.loads((source/'状态.json').read_text())
    assert len(Path(state['标签']).parts)==1 and state['标签'] not in ('.','..'), '旧程序标签越界'
    binary=source/state['标签']/'db_bench'
    assert binary.is_file() and digest(binary)==proof['程序SHA256'], '旧可运行程序身份不符'
    # 源配方必须与实际归档相同；不静默混入当前工作区的其他修改。
    for name in ('end_to_end_source.py','overlap_source.py','phase_time_source.py',
                 'read_cost_source.py','calibration_source.py'):
        assert digest(Path(__file__).parent/name)==digest(source/'源码'/name), '原构建配方已变化：'+name
    output.mkdir(parents=True)
    old=output/'旧G2源码';new=output/'新版源码'
    old.mkdir();new.mkdir()
    with tarfile.open(archive) as tar:
        for item in tar:
            if not item.isfile() or not item.name.endswith(('.cc','.h','.inc')):continue
            parts=Path(item.name).parts
            assert parts[:4]==('协同池副本','plugin','zenfs','fs') and len(parts)==5, '源归档路径非法'
            data=tar.extractfile(item).read()
            assert hashlib.sha256(data).hexdigest()==materials['文件'][item.name], '源文件身份不同'
            with (old/parts[-1]).open('xb') as f:f.write(data)
    # 按本轮已归档构建配方复原旧G2的区间观测；不生成或重跑客户端负载。
    for name,fn in (('fs_zenfs.h',timing.fs_header),('metrics.h',timing.metrics),
                    ('zbd_zenfs.cc',timing.zbd),('io_zenfs.cc',timing.io),('fs_zenfs.cc',timing.fs)):
        p=old/name;p.write_text(fn(p.read_text()))
    (old/'rrzc_phase_time.h').write_bytes((source/'源码/rrzc_phase_time.h').read_bytes())
    for p in old.iterdir():
        text=p.read_text()
        (new/p.name).write_text(change.TRANSFORMS.get(p.name,lambda s:s)(text))
    (new/'rrzc_placement_mode.h').write_bytes((ROOT/'model/rrzc_placement_mode.h').read_bytes())
    assert change.undo_adapter((new/'rrzc_colocation_zenfs.inc').read_text())==(old/'rrzc_colocation_zenfs.inc').read_text()
    allowed=set(change.TRANSFORMS)
    for p in old.iterdir():
        if p.name not in allowed:assert digest(p)==digest(new/p.name)
    # 原可运行程序只引用和核验，绝不覆盖或重新存放完整镜像。
    difference=[]
    for p in sorted(old.iterdir()):
        difference.extend(difflib.unified_diff(p.read_text().splitlines(True),
            (new/p.name).read_text().splitlines(True),fromfile='旧G2/'+p.name,tofile='新版/'+p.name))
    difference.extend(difflib.unified_diff([], (new/'rrzc_placement_mode.h').read_text().splitlines(True),
        fromfile='/dev/null',tofile='新版/rrzc_placement_mode.h'))
    (output/'代码差异.patch').write_text(''.join(difference))
    report=dict(来源=str(source),冻结源SHA256=digest(archive),原库SHA256=materials['原库SHA256'],
        原程序=str(binary),原程序SHA256=proof['程序SHA256'],
        旧源复原方式='归档冻结源码＋同一归档的端到端构建配方；不是新增NAND快照',
        允许变化=sorted(allowed|{'rrzc_placement_mode.h'}),
        旧源码={p.name:digest(p) for p in old.iterdir()},
        新源码={p.name:digest(p) for p in new.iterdir()},
        策略正文逆变换一致=True,完整ZenFS编译=False,客机启动=False,业务实验=False)
    (output/'副本身份.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    # 独立构建必须重编同ABI的全部五单元，不能混用带新成员与旧成员布局的对象。
    (output/'构建说明.md').write_text('本目录只保存源码及身份，不启动设备。\n'
        '完整编译：build_placement_ablation.py --compile 独立副本 --rocksdb-root 客机构建根目录\n'
        '旧库保持原文件；编译五个ZenFS单元至本目录并合并为独立对象，不链接或运行db_bench。\n')
    return report


def compile_overlay(output,rocksdb):
    from build_phase_time_guest import command
    report=json.loads((output/'副本身份.json').read_text());library=rocksdb/'librocksdb_debug.a'
    assert digest(library)==report['原库SHA256'], '完整构建环境的原库身份不一致'
    for name,value in report['新源码'].items():
        assert digest(output/'新版源码'/name)==value, '独立源副本已改变：'+name
    # 编译参数原有依赖输出必须重定向，避免编译新副本时覆盖原树的.d文件。
    units=('fs_zenfs','io_zenfs','zbd_zenfs','zbdlib_zenfs','zonefs_zenfs')
    objects=output/'编译对象';objects.mkdir()
    commands=[]
    for name in units:
        relative='plugin/zenfs/fs/'+name+'.cc'
        dry=subprocess.check_output(['make','-n','DEBUG_LEVEL=1','LIB_MODE=static','USE_RTTI=0',
            'ROCKSDB_PLUGINS=zenfs','-W',relative,relative[:-3]+'.o'],cwd=rocksdb,text=True,stderr=subprocess.STDOUT)
        argv=command(dry,relative);argv[argv.index(relative)]=str((output/'新版源码'/(name+'.cc')).resolve())
        argv[argv.index('-o')+1]=str((objects/(name+'.o')).resolve())
        compiler=next(i for i,a in enumerate(argv) if Path(a).name=='g++')
        argv[compiler+1:compiler+1]=['-I'+str((output/'新版源码').resolve())];argv+=['-g0']
        argv=isolate_dependencies(argv,objects,name)
        commands.append(argv)
        with (objects/(name+'.log')).open('x') as log:
            subprocess.run(argv,cwd=rocksdb,stdout=log,stderr=subprocess.STDOUT,check=True)
    subprocess.run(['ld','-r','-o',str(objects/'放置消融ZenFS.o'),
        *[str(objects/(n+'.o')) for n in units]],check=True)
    assert digest(library)==report['原库SHA256']
    (objects/'命令.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n')
    report['完整ZenFS编译']=True;report['独立合并对象SHA256']=digest(objects/'放置消融ZenFS.o')
    (output/'副本身份.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=SOURCE)
    p.add_argument('--output',type=Path)
    p.add_argument('--compile',type=Path,dest='compile_dir')
    p.add_argument('--rocksdb-root',type=Path)
    args=p.parse_args()
    if args.compile_dir:
        assert args.rocksdb_root and not args.output
        compile_overlay(args.compile_dir.resolve(),args.rocksdb_root.resolve())
    else:
        assert args.output and not args.rocksdb_root
        print(json.dumps(prepare(args.source,args.output),ensure_ascii=False,indent=2))
