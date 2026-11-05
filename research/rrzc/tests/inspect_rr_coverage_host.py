#!/usr/bin/env python3
"""隔离客机只读采集RR覆盖竞态相关的实际ZenFS源，归档后正常关机。"""
import getpass
import json
from pathlib import Path
from types import SimpleNamespace
from run_dbbench_parallel_host import DbRun,ROOT,IMAGE_DIR,capacity,stamp
from run_tail_ablation_fixed_pair import running_parent
from run_joint_host import digest_manifest
from ycsb_af import require,digest

LABEL='joint-rr-coverage-inspect-v1-20261024'
SOURCE=IMAGE_DIR/'joint-dbbench8-rotating-v2-20261024-results'
PARENT=IMAGE_DIR/'joint-dbbench8-rotating-v2-20261024-prepare.qcow2'


def main():
    require(PARENT.is_file() and not running_parent(PARENT),'父镜像不可用')
    capacity();output=IMAGE_DIR/(LABEL+'-results');require(not output.exists(),'不覆盖检查记录')
    password=getpass.getpass('覆盖缺口源码检查认证（仅内存）：')
    output.mkdir();(output/'源码').mkdir();run=DbRun(SimpleNamespace(label=LABEL),password,output,'inspect',8101,PARENT)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),180))==expected,
                '源镜像身份不符')
        for name in ('io_zenfs.cc','io_zenfs.h','fs_zenfs.cc','fs_zenfs.h','zbd_zenfs.cc','zbd_zenfs.h','snapshot.h'):
            remote='/home/femu/rocksdb/plugin/zenfs/fs/'+name
            run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(output/'源码'/name)])
            if 'plugin/zenfs/fs/'+name in expected:
                require(digest(output/'源码'/name)==expected['plugin/zenfs/fs/'+name],'取回源码哈希不同')
        stamp(output/'同库身份.json',expected)
        run.record(阶段='实际源码已只读采集，未修改客机代码或模型')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        finally:password=None
    print(str(output/'源码'))


if __name__=='__main__':main()
