"""从第一阶段已冻结副本生成第二阶段副本；不覆盖旧代码，不启动设备。"""
import argparse
import difflib
import json
from pathlib import Path
import placement_trace_source as change
from build_placement_ablation import ROOT,digest,compile_overlay

SOURCE=ROOT/'results/placement-ablation-20261105/阶段一'


def prepare(source,output):
    assert not output.exists(),'不覆盖第二阶段副本'
    identity=json.loads((source/'副本身份.json').read_text())
    for name,h in identity['新源码'].items():assert digest(source/'新版源码'/name)==h,'阶段一来源已变化'
    output.mkdir(parents=True);new=output/'新版源码';new.mkdir()
    delta=[]
    for name,h in identity['新源码'].items():
        old=(source/'新版源码'/name).read_text()
        result=change.TRANSFORMS.get(name,lambda s:s)(old)
        (new/name).write_text(result)
        delta.extend(difflib.unified_diff(old.splitlines(True),result.splitlines(True),
            fromfile='阶段一/'+name,tofile='阶段二/'+name))
    header=ROOT/'model/rrzc_placement_trace.h'
    (new/header.name).write_bytes(header.read_bytes())
    delta.extend(difflib.unified_diff([],header.read_text().splitlines(True),
        fromfile='/dev/null',tofile='阶段二/'+header.name))
    (output/'代码差异.patch').write_text(''.join(delta))
    report=dict(来源=str(source),原程序=identity['原程序'],原程序SHA256=identity['原程序SHA256'],
        原库SHA256=identity['原库SHA256'],阶段一源码=identity['新源码'],
        新源码={p.name:digest(p) for p in new.iterdir()},
        观测配置='ZENFS_RRZC_PLACEMENT_TRACE=独立的新输出文件；三组共同配置',
        完整ZenFS编译=False,客机启动=False,业务实验=False,账目参与策略=False)
    (output/'副本身份.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=SOURCE);p.add_argument('--output',type=Path)
    p.add_argument('--compile',type=Path,dest='compile_dir');p.add_argument('--rocksdb-root',type=Path)
    a=p.parse_args()
    if a.compile_dir:
        assert a.rocksdb_root and not a.output
        compile_overlay(a.compile_dir.resolve(),a.rocksdb_root.resolve())
    else:
        assert a.output and not a.rocksdb_root
        print(json.dumps(prepare(a.source,a.output),ensure_ascii=False,indent=2))
