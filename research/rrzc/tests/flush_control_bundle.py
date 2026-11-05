"""冻结已验收来源程序；开关对照不能偷偷重新编译或混入旧ABI。"""
import json
from pathlib import Path
import shutil
import tarfile
from sampling_scope_bundle import closed
from run_pool_dbbench_host import IMAGE_DIR, ROOT
from ycsb_af import digest, require

PRIOR = IMAGE_DIR / 'joint-controlled-sst-flush-w-v3-20261030-results'


def disabled_guard(root):
    """关闭组必须有真实Flush，且不能创建或恢复来源W。"""
    from analyze_sst_compaction import events
    for name in ('run.log', 'verify.log', 'run.zenfs.log', 'verify.zenfs.log'):
        text = (root/name).read_text()
        require('Flush来源W标记：' not in text and 'Flush来源挂载文件：' not in text,
                '关闭组意外存在Flush来源：'+name)
    require(any(e['记录'].get('event') == 'flush_finished' for e in events(root/'run.rocksdb.log')),
            '关闭组没有真实Flush，不足以核验开关')


def freeze(output, prior=PRIOR):
    guest = closed(prior)
    repeat = json.loads((prior/'Greedy重复核验.json').read_text())
    build = json.loads((guest/'生命周期构建核验.json').read_text())
    plan = json.loads((guest/'冻结对象清单.json').read_text())
    require(repeat['通过'] and build['通过'] and build['元数据版本'] == 11
            and build['ZenFS完整五单元重编'], '来源程序未通过完整验收')
    require(digest(guest/'db_bench') == build['程序SHA256']
            and digest(guest/'fs_colocation.o') == plan['对象SHA256'] == build['冻结ZenFS对象SHA256']
            and digest(guest/'冻结对象.tar.gz') == plan['压缩包SHA256'], '来源程序或同ABI对象不同')
    source = output/'源码'
    require(json.loads((source/'expected.json').read_text()) == json.loads((guest/'expected.json').read_text()),
            '本轮原软件身份与来源不同')
    for name in ('冻结对象.tar.gz', '冻结对象清单.json'):
        shutil.copyfile(guest/name, source/name)
    files = {n: guest/n for n in ('db_bench', 'space_probe', 'flush_metadata_probe',
              '生命周期构建核验.json', '校准-db_bench_tool.cc', '诊断-flush_job.cc', 'rrzc_sst_lifetime.h')}
    payload = source/'Flush来源复用.tar.gz'
    with tarfile.open(payload, 'x:gz') as tar:
        for name, path in files.items():
            require(path.is_file() and not path.is_symlink(), '复用成员不是普通文件')
            tar.add(path, arcname=name, recursive=False)
    state = json.loads((prior/'状态.json').read_text())
    record = dict(来源=str(prior), 来源完整归档SHA256=state['镜像处理']['archive_sha256'],
        文件={n:digest(p) for n,p in files.items()}, 压缩包SHA256=digest(payload),
        对象SHA256=plan['对象SHA256'], 原库SHA256=plan['原库SHA256'],
        程序SHA256=build['程序SHA256'], 请求源码SHA256=build['新客户端源码SHA256'],
        边界='仅软件逐字复用；运行状态独立Reset并装载，非模拟NAND内存检查点')
    (source/'Flush来源复用清单.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    scorer = source/'宿主评分核'
    shutil.copytree(prior/'源码/宿主评分核', scorer)
    identity = json.loads((scorer/'编译身份.json').read_text())
    require(digest(scorer/'维护核重算') == identity['二进制SHA256'], '评分核身份不同')
    for name, sha in identity['头文件'].items():
        require(digest(scorer/name) == digest(ROOT/'model'/name)
                == digest(guest/'协同池副本/plugin/zenfs/fs'/name) == sha, '模型头不同：'+name)
    return scorer/'维护核重算'
