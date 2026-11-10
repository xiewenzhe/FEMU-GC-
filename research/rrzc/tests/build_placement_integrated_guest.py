"""第三阶段独立集成构建：统一头文件重编五个ZenFS单元、Flush支持和原客户端。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path('/home/femu/rocksdb')


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(target):
    manifest=json.loads((target/'传输身份.json').read_text())
    assert all(digest(target/n)==h for n,h in manifest.items()),'传输身份不符'
    identity=json.loads((target/'二阶副本身份.json').read_text())
    library=ROOT/'librocksdb_debug.a';before=digest(library)
    assert before==identity['原库SHA256'],'原静态库身份不符'
    fs=target/'协同池副本/plugin/zenfs/fs'
    assert all(digest(fs/n)==h for n,h in identity['新源码'].items()),'冻结副本身份不符'
    old='/home/femu/joint-controlled-integrated-pool-v2-20261104'
    objects=target/'计时对象';objects.mkdir()
    def native(name):return [x.replace(old,str(target)) for x in json.loads((target/(name+'.原命令.json')).read_text())]
    def run(argv,name):
        # 只执行结构化argv；禁止原命令中混入shell操作。
        assert not any(x in argv for x in (';','&&','||','|','>'))
        (target/(name+'.命令.json')).write_text(json.dumps(argv,ensure_ascii=False)+'\n')
        with (target/(name+'.log')).open('x') as out:
            subprocess.run(argv,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=900)
    units=('fs_zenfs','io_zenfs','zbd_zenfs','zbdlib_zenfs','zonefs_zenfs')
    for name in units:run(native('计时编译-'+name),'编译-'+name)
    run(['ld','-r','-o',str(target/'计时ZenFS.o'),*[str(objects/(n+'.o')) for n in units]],'ZenFS合并')
    # 旧Flush对象的Enabled内联函数读取旧开关；必须与显式模式使用同一新头。
    flush=native('计时编译-fs_zenfs')
    flush[flush.index('-c')+1]=str(target/'诊断-flush_job.cc')
    flush[flush.index('-o')+1]=str(target/'诊断-flush_job.cc.o')
    run(flush,'Flush重编')
    run(native('计时客户端编译'),'客户端重编')
    run(native('计时客户端链接'),'客户端链接')
    mapping=(target/'计时链接映射.txt').read_text().split('Discarded input sections',1)[0]
    assert all('librocksdb_debug.a('+n+')' not in mapping for n in (*[n+'.o' for n in units],'flush_job.o')),'混入旧布局对象'
    (target/'计时-db_bench').rename(target/'db_bench')
    if (target/'放置设备小测.cc').exists():
        cc=native('计时编译-fs_zenfs');cc[cc.index('-c')+1]=str(target/'放置设备小测.cc')
        cc[cc.index('-o')+1]=str(target/'设备小测.o');run(cc,'设备小测编译')
        link=native('计时客户端链接')
        link=[x for x in link if x not in ('tools/db_bench.o','tools/tool_hooks.o',
            'tools/simulated_hybrid_file_system.o','test_util/testutil.o')]
        link=[str(target/'设备小测.o') if x==str(target/'计时客户端.o') else
              '-Wl,-Map,'+str(target/'设备小测链接映射.txt') if x.startswith('-Wl,-Map,') else x for x in link]
        link[link.index('-o')+1]=str(target/'placement_probe');run(link,'设备小测链接')
    assert digest(library)==before
    assert all(digest(fs/n)==h for n,h in identity['新源码'].items()),'构建改动冻结源'
    report=dict(通过=True,完整五单元编译=True,Flush统一头重编=True,客户端统一头重编=True,
        原库保持不变=True,程序SHA256=digest(target/'db_bench'),ZenFS对象SHA256=digest(target/'计时ZenFS.o'),
        原库SHA256=before,新Flush对象SHA256=digest(target/'诊断-flush_job.cc.o'),
        业务实验=False,边界='完整集成构建与链接，不代表设备生命周期或性能回归通过')
    if (target/'placement_probe').exists():report['设备小测SHA256']=digest(target/'placement_probe')
    (target/'集成构建核验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('五单元、Flush和客户端独立集成构建通过，原库未覆盖',flush=True)


if __name__=='__main__':build(Path(sys.argv[1]).resolve())
