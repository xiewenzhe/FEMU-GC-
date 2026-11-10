"""只在本次独立空FEMU盘执行RocksDB小测和纯RR/重挂载功能测试；非收益实验。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def exercise(target,mode):
    assert os.geteuid()==0 and mode in ('G0','G1','G2')
    def query(argv):return subprocess.check_output(argv,text=True)
    assert query(['blockdev','--getsize64','/dev/nvme0n1']).strip()=='8589934592','拒绝操作非8GiB测试设备'
    initial=json.loads(query(['nvme','zns','report-zones','/dev/nvme0n1','-o','json']))
    assert len(initial['zone_list'])==64 and all(z['state']=='EMPTY' and z['wp']==z['slba'] for z in initial['zone_list']), '必须从新客机空NAND开始'
    identify=json.loads(query(['nvme','zns','id-ns','/dev/nvme0n1','-o','json']))
    assert identify['mor']+1==8 and identify['mar']+1==12,'资源配置不符'
    (target/'设备初始.json').write_text(json.dumps(dict(Zone=initial,Identify=identify),ensure_ascii=False)+'\n')
    scheduler=Path('/sys/block/nvme0n1/queue/scheduler')
    before=scheduler.read_text();scheduler.write_text('mq-deadline\n')
    after=scheduler.read_text();assert '[mq-deadline]' in after
    (target/'客机调度器.json').write_text(json.dumps(dict(原值=before,本轮=after,
        范围='仅已确认的本次独立8GiB FEMU设备；沿用ZenFS要求，不改宿主系统'),ensure_ascii=False)+'\n')
    env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',LANG='C.UTF-8',ZENFS_RRZC_PLACEMENT_MODE=mode,
        ZENFS_RR_STRICT='1',ZENFS_RR_ENABLE='1',ZENFS_MAINT_DIAG='1',ZENFS_ALLOC_DIAG='1',
        ZENFS_ALLOC_FALLBACK='1',ZENFS_MIGRATE_RESERVE='1',ZENFS_RRZC_CONTENT_GUARD='1',
        ZENFS_RRZC_MIGRATION_SPARE='1',ZENFS_RRZC_EMPTY_RESERVE='1',ZENFS_RRZC_RESERVE_TARGET='1',
        ZENFS_RRZC_SINGLE_RECOVERY='1',ZENFS_RR_FAIR_QUEUE='1',ZENFS_RRZC_SIMPLE_MODE='valid',
        ZENFS_RRZC_RECLAIM_BYTES='0',ZENFS_RRZC_V1_SHADOW='1')
    def run(argv,name,active=None):
        with (target/(name+'.log')).open('x') as log:
            subprocess.run(argv,env=active or env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    db=target/'数据库小测';db.mkdir();(db/'aux').mkdir()
    zenfs='/home/femu/rocksdb/plugin/zenfs/util/zenfs'
    run([zenfs,'mkfs','--zbd=nvme0n1','--aux_path='+str(db/'aux'),'--enable_gc'],'数据库格式化')
    active=dict(env,ZENFS_RRZC_PLACEMENT_TRACE=str(target/'数据库组成.bin'))
    start=time.time_ns()
    run([str(target/'db_bench'),'--fs_uri=zenfs://dev:nvme0n1','--db=/placement-smoke',
        '--benchmarks=fillseq,readseq,verifychecksum','--num=8192','--reads=8192','--value_size=4096',
        '--compression_type=none','--disable_wal=1','--cache_size=0','--row_cache_size=0',
        '--threads=1','--seed=20261023','--write_buffer_size=8388608','--target_file_size_base=8388608'],
        '数据库小测',active)
    logs=target/'数据库设备日志';logs.mkdir()
    for path in Path('/tmp').glob('zenfs_nvme0n1*'):
        if path.is_file() and path.stat().st_mtime_ns>=start:shutil.copyfile(path,logs/path.name)
    # 隔离纯RR功能载荷：前一小测已经退出，只重置本次独立FEMU设备。
    run(['nvme','zns','reset-zone','/dev/nvme0n1','-a'],'纯RR前独立盘Reset')
    rr=target/'RR功能';rr.mkdir();(rr/'aux').mkdir()
    run([zenfs,'mkfs','--zbd=nvme0n1','--aux_path='+str(rr/'aux')],'RR格式化')
    active=dict(env,ZENFS_RRZC_PLACEMENT_TRACE=str(target/'RR组成.bin'))
    run([str(target/'placement_probe'),str(rr)],'RR与重挂载小测',active)
    (target/'设备功能完成.json').write_text(json.dumps(dict(通过=True,模式=mode,
        实际Open=8,实际Active=12,实际容量GiB=8,RR阈值=1024,纯RR临时关闭GC=True,
        完整业务实验=False,说明='真实RocksDB32MiB小测及合成3MiB文件纯RR/重挂载功能；不代表收益'),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':exercise(Path(sys.argv[1]),sys.argv[2])
