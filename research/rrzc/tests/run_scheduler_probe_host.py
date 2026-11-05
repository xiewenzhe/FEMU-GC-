#!/usr/bin/env python3
"""只读核对实际RocksDB后台调度代码，并在普通目录验证单后台线程配置。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shlex
import shutil

from run_calibration_host import CalibrationRun, IMAGE, PARENT
from run_rr_path_host import preflight, IMAGE_DIR
from run_triggered_gc_host import preserve_unverified_image
from run_dbbench_parallel_host import stamp
from ycsb_af import require


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-scheduler-probe-v1-20261027')
    parser.add_argument('--sources-only',action='store_true',help='只读采集部署源码，不运行负载')
    args=parser.parse_args()
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label),'标签非法')
    budget=preflight();output=IMAGE_DIR/(args.label+'-results')
    require(not output.exists(),'不覆盖已有结果')
    password=getpass.getpass('后台调度核验认证（仅内存）：')
    output.mkdir();shutil.copyfile(__file__,output/Path(__file__).name)
    stamp(output/'预注册计划.json',dict(目的='只读实际库调度代码，普通目录核对单后台线程是否生效',
        资源=budget,仅采集源码=args.sources_only,边界='不运行ZNS业务，不更改库或SSD参数，不作性能结论'))
    run=CalibrationRun(args,password,output,'scheduler',8101,PARENT,retain_image=False)
    run.args_reuse_image=IMAGE;error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.record(阶段='读取部署库的调度实现')
        source=r'''
from pathlib import Path
import hashlib,json,shutil,sys
root=Path('/home/femu/rocksdb');dest=Path(sys.argv[1])/'部署源码';dest.mkdir()
manifest={};missing=[]
for name in ('db/db_impl/db_impl.cc','db/db_impl/db_impl_compaction_flush.cc',
             'options/db_options.cc','include/rocksdb/options.h','util/threadpool_imp.cc',
             'db/flush_job.cc','db/builder.cc','file/file_util.cc','util/logging.cc',
             'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/io_zenfs.cc',
             'plugin/zenfs/fs/zbd_zenfs.cc','plugin/zenfs/fs/zbdlib_zenfs.cc'):
    src=root/name
    if not src.is_file():missing.append(name);continue
    shutil.copyfile(src,dest/name.replace('/','__'))
    manifest[name]=hashlib.sha256(src.read_bytes()).hexdigest()
assert 'db/flush_job.cc' in manifest and 'plugin/zenfs/fs/fs_zenfs.cc' in manifest,'关键部署源码缺失'
(dest/'来源.json').write_text(json.dumps(dict(文件SHA256=manifest,未找到=missing),ensure_ascii=False,indent=2)+'\n')
'''
        run.ssh('python3 -c '+shlex.quote(source)+' '+shlex.quote(run.target))
        if not args.sources_only:
            run.record(阶段='普通目录短测：Flush与Compaction共用单线程')
            run.ssh('/home/femu/rocksdb/db_bench '+f'--db={run.target}/posix '+
                '--benchmarks=fillrandom,flush,waitforcompaction,readseq,verifychecksum '+
                '--num=65536 --value_size=1024 --compression_type=none --disable_wal=1 '+
                '--write_buffer_size=4194304 --max_write_buffer_number=2 --seed=20261023 '+
                '--max_background_jobs=1 --max_background_compactions=1 --max_background_flushes=0 '+
                '--num_low_pri_threads=1 --num_high_pri_threads=0 '+
                '--target_file_size_base=4194304 --max_bytes_for_level_base=16777216 '+
                f'> {run.target}/普通目录短测.log 2>&1',180)
            run.ssh(f'cp {run.target}/posix/LOG {run.target}/后台事件.log')
        run.record(阶段='采证结束，归档并关机')
    except BaseException as exc:
        error=exc;run.record(原始错误=repr(exc));raise
    finally:
        try:run.finish(error)
        except Exception as exc:
            if not preserve_unverified_image(run,exc):raise
        password=None


if __name__=='__main__':main()
