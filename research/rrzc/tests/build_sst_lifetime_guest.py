"""只编译生命周期db_bench客户端；冻结ZenFS、原库及旧负载保持不变。"""
import json
from pathlib import Path
import subprocess
import sys
from build_pool_dbbench_guest import build as build_client
from build_all_blocks_guest import ROOT,digest
from sst_lifetime_source import patched


def validate_mode(target):
    assert not (target/'同步重编清单.json').exists(),'生命周期负载禁止重编维护对象'
    reused=target/'复用真实DB程序.json'
    if reused.exists():
        record=json.loads(reused.read_text())
        assert record['生命周期客户端源码SHA256']==digest(target/'校准-db_bench_tool.cc'),'复用客户端源码不同'
        assert record['生命周期计划头SHA256']==digest(target/'rrzc_sst_lifetime.h'),'复用请求计划不同'


def build(target):
    path=target/'校准-db_bench_tool.cc';original=path.read_text()
    (target/'既有-db_bench_tool.cc').write_text(original);path.write_text(patched(original))
    validate_mode(target)
    build_client(target)
    smoke=target/'生命周期普通目录小测.log'
    command=['env','RRZC_SST_LIFETIME_SMOKE=1','RRZC_CAL_TLS_SEED=1','RRZC_CAL_SINGLE_BACKGROUND=1',
        'RRZC_CAL_REQUEST_TRACE='+str(target/'生命周期小测请求.bin'),str(target/'db_bench'),
        '--db='+str(target/'生命周期小测库'),'--benchmarks=fillseq,rrzcsstlifetime,readseq,verifychecksum',
        '--num=1024','--value_size=4096','--compression_type=none','--disable_wal=1','--cache_size=0',
        '--row_cache_size=0','--threads=1','--seed=20261023','--reads=1024','--writes=480']
    with smoke.open('w') as log:subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    assert 'SST生命周期完成：读=256，写=480，检查点=10' in smoke.read_text()
    # 两种模式使用同一已编译程序，固定H模式另做真实Get/Put和完整内容校验。
    persistent=[arg.replace('生命周期小测请求.bin','固定H小测请求.bin').replace('生命周期小测库','固定H小测库')
                for arg in command]
    persistent.insert(1,'RRZC_SST_LIFETIME_PERSISTENT_H=1')
    with (target/'固定H普通目录小测.log').open('w') as log:
        subprocess.run(persistent,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    assert 'SST生命周期计划：固定H=1' in (target/'固定H普通目录小测.log').read_text()
    assert 'SST生命周期完成：读=256，写=480，检查点=10' in (target/'固定H普通目录小测.log').read_text()
    identity=dict(通过=True,普通目录小测通过=True,固定H普通目录小测通过=True,原客户端源码SHA256=digest(target/'既有-db_bench_tool.cc'),
        新客户端源码SHA256=digest(path),计划头SHA256=digest(target/'rrzc_sst_lifetime.h'),
        程序SHA256=digest(target/'db_bench'),
        冻结ZenFS对象SHA256=digest(target/'fs_colocation.o'),
        边界='新定制负载不代表YCSB；只有客户端变化，必须重新做Greedy重复；未强制CompactRange或删除SST')
    (target/'生命周期构建核验.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':build(Path(sys.argv[1]))
