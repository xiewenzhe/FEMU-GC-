"""登记采样修正所需的原逐编译单元，不借用旧二进制冒充新构建。"""
import json
from pathlib import Path
import tarfile
from ycsb_af import digest,require
from run_pool_dbbench_host import VERIFIED_DB,IMAGE_DIR


def closed(root):
    s=json.loads((root/'状态.json').read_text());a=json.loads((root/'阶段归档.json').read_text())
    require(a['完整'] and a['业务成功'] and not a['客机仍存活'] and s['当前客机PID'] is None,'组件来源未成功归档退出')
    require(digest(Path(s['镜像处理']['archive']))==s['镜像处理']['archive_sha256'],'组件来源归档不同')
    return root/s['标签']


def freeze(output,prior,identity,build):
    source=closed(VERIFIED_DB)
    native=closed(IMAGE_DIR/'joint-controlled-sst-persistent-v1-20261030-results')
    frontend=json.loads((native/'生命周期构建核验.json').read_text())
    require(frontend['通过'] and digest(native/'db_bench')==frontend['程序SHA256']==identity['程序SHA256']
        and frontend['新客户端源码SHA256']==identity['新客户端源码SHA256'] and
        frontend['计划头SHA256']==identity['计划头SHA256'],'复用前端与已验收请求程序不同')
    components={}
    for p in (source/'复用ZenFS对象').glob('*.o'):components['采样组件/ZenFS/'+p.name]=p
    require(len(components)==5,'ZenFS逐编译单元不完整')
    for name in ('客户端.o','诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o'):
        p=native/name;require(p.is_file(),'客户端组件缺失');components['采样组件/前端/'+name]=p
    payload=output/'源码/采样组件.tar.gz'
    with tarfile.open(payload,'x:gz') as tar:
        for name,path in sorted(components.items()):tar.add(path,arcname=name,recursive=False)
    manifest=dict(文件={name:digest(path) for name,path in components.items()},压缩包SHA256=digest(payload),
        原ZenFS对象SHA256=build['冻结ZenFS对象SHA256'],原库SHA256=build['原库SHA256'],
        原客户端源码SHA256=identity['原客户端源码SHA256'],客户端源码SHA256=identity['新客户端源码SHA256'],
        计划头SHA256=identity['计划头SHA256'],请求前端对象SHA256=digest(native/'客户端.o'),
        来源=dict(ZenFS=str(VERIFIED_DB),前端=str(native.parent),本轮请求程序参考=str(prior)),
        说明='只重编采样所在fs_zenfs.cc，其余对象及请求逻辑逐字复用；重新链接后是新程序')
    (output/'源码/采样组件清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
