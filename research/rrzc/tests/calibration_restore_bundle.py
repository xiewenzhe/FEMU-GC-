"""从宿主已验收归档构造最小软件包；仅供新登记临时镜像恢复。"""
import json
from pathlib import Path
import tarfile
from ycsb_af import digest,require


def freeze_bundle(output,previous,donor,client,metadata_reference=True):
    source=donor/donor.name.removesuffix('-results')
    expected=json.loads((output/'源码/expected.json').read_text())
    baseline=previous/previous.name.removesuffix('-results')
    names={'plugin/zenfs/fs/fs_zenfs.cc':previous/'源码/修复后-fs_zenfs.cc',
           'plugin/zenfs/fs/zbd_zenfs.cc':previous/'源码/修复后-zbd_zenfs.cc',
           'plugin/zenfs/fs/zbd_zenfs.h':previous/'源码/修复后-zbd_zenfs.h',
           'plugin/zenfs/fs/rrzc_direct_zenfs.inc':source/'共同观测-原始选区.inc',
           'plugin/zenfs/fs/rrzc_triggered_gc.h':previous/'源码/rrzc_triggered_gc.h'}
    files={};repair={}
    for name,path in names.items():
        require(path.is_file() and digest(path)==expected[name],'历史源码身份不符：'+name)
        member='native/'+Path(name).name;files[member]=path;repair[name]=member
    for name,sha in client['文件SHA256'].items():
        path=source/name;require(digest(path)==sha,'客户端身份不符：'+name)
        files['client/'+name]=path
    # 独立校准客户端之外，设备查询/空间工具也直接复用旧已验收程序。
    for name in ('space_probe','all_blocks_device','reserve_restore_device'):
        path=baseline/name;require(path.is_file(),'原设备工具缺失：'+name)
        files['tools/'+name]=path
    if metadata_reference:
        metadata=output/'源码/元数据起点目标.json'
        rows=json.loads((source/'g1/blocks.before.json').read_text())
        metadata.write_text(json.dumps([r for r in rows if r['Zone']<3],ensure_ascii=False,indent=2)+'\n')
        files['tools/'+metadata.name]=metadata
    archive=output/'源码/恢复文件.tar.gz'
    with tarfile.open(archive,'x:gz') as tar:
        for member,path in files.items():tar.add(path,arcname=member,recursive=False)
    manifest=dict(来源实验=str(donor),来源完整归档SHA256=client['来源归档SHA256'],
        原软件恢复=repair,文件={member:digest(path) for member,path in files.items()},
        压缩包SHA256=digest(archive),边界='软件恢复包；不包含逻辑SSD数据、NAND映射或RC')
    (output/'源码/宿主恢复清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest
