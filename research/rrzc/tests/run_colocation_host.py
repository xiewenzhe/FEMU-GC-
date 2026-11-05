"""基线验收后，在唯一可销毁客机验证固定K=1；原库及旧文件系统不覆盖。"""
import argparse
import csv
import getpass
import json
from pathlib import Path
import re
import shutil
import signal

from run_calibration_host import (CalibrationRun,PREVIOUS,PARENT,ROOT,IMAGE_DIR,
    check_baseline_budget,detach_supervisor)
from run_repeat_order_host import freeze
from calibration_restore_bundle import freeze_bundle
from run_rr_path_host import preflight
from run_ycsb_quick_host import profile8
from run_dbbench_parallel_host import stamp
from run_triggered_gc_host import wait_stage,stop_business,preserve_unverified_image
from ycsb_af import digest,require

BASELINE=IMAGE_DIR/'joint-controlled-pool-baseline-v3-20261029-results'
DONOR=IMAGE_DIR/'joint-controlled-fragments-repeat-v2-20261028-results'
BINARY=ROOT/'results/observation-alignment-20261028/ftl-fragments-build-v1/诊断程序/qemu-system-x86_64'

GUEST=r'''#!/usr/bin/env bash
# 只作用于本次8GiB临时FEMU模拟盘；每组独立重新格式化，不触及宿主或旧实验盘。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
tag=${RRZC_ARM_TAG:?}
[[ $(id -u) == 0 && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ $tag == reference || $tag == disabled || $tag == enabled ]]
result=$target/$tag
[[ ! -e $result ]]
mkdir "$result" "$result/aux"
trap 'code=$?; echo "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
echo prepare > "$result/phase.txt"
nvme zns id-ns /dev/nvme0n1 -o json > "$result/设备身份.json"
python3 - "$result/设备身份.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));assert r['mar']==11 and r['mor']==7
PY
nvme zns reset-zone /dev/nvme0n1 -a > "$result/清空模拟盘.log" 2>&1
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cd /home/femu/rocksdb
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" > "$result/mkfs.log" 2>&1
program=$target/colocation_prototype
k=0
if [[ $tag == reference ]]; then program=$target/colocation_reference; fi
if [[ $tag == enabled ]]; then k=1; fi
echo run > "$result/phase.txt"
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 \
  ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RR_FAIR_QUEUE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 ZENFS_RRZC_V1_SHADOW=1 \
  ZENFS_RRZC_POOL_K="$k" ZENFS_RRZC_POOL_TAGS="$target/受控标签.txt" \
  "$program" "$result" "$k" > "$result/run.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
echo done > "$result/phase.txt"
'''


def admission(baseline):
    proof=json.loads((baseline/'新资源Greedy核验.json').read_text())
    archive=json.loads((baseline/'阶段归档.json').read_text())
    state=json.loads((baseline/'状态.json').read_text())
    require(proof['通过'] and proof['资源上限']==dict(Open=8,Active=12),
            '新Open/Active Greedy未验收，禁止部署协同池')
    require(archive['完整'] and not archive['客机仍存活'] and state['当前客机PID'] is None,
            '基线尚未归档退出，不能叠加原型客机')
    payload=baseline/(state['标签']+'-calibration.tar.gz')
    require(digest(payload)==state['镜像处理']['archive_sha256'],'基线归档哈希不符')
    return proof


def analyze(output):
    source=output/output.name.removesuffix('-results')
    def layout(tag):
        with (source/tag/'布局.csv').open() as stream:return list(csv.DictReader(stream))
    original,disabled=layout('reference'),layout('disabled')
    require(original==disabled,'新对象K=0与原对象布局不同，不验收原型')
    enabled=(source/'enabled/run.log').read_text()
    require('协同池真实功能通过' in enabled,'K=1完整功能未通过')
    log=(source/'enabled/zenfs.log').read_text()
    require('阶段=新文件' in log and '阶段=必要迁移' in log and '协同池恢复：成员=' in log,
            '缺少新文件、必要迁移或恢复接线证据')
    require('启动 Zone 维护线程：GC=0，主机端 RR=1' in log and 'ZC 统计：' not in log,
            '纯功能实验意外改变GC配置')
    result=dict(通过=True,K0布局与原对象一致=True,K1同区及恢复与RR路径=True,
        边界='受控ZenFS载荷而非RocksDB表；尚未证明收益、正常并发或K1下实际ZC维护性能')
    stamp(output/'固定K1功能核验.json',result)
    (output/'阶段二报告.md').write_text('# 固定K=1最小功能闭环\n\n'
        '原对象与新对象K=0布局一致；K=1真实验证H/W同区、普通文件隔离、Closed重挂载复用、'
        '必要RR迁移、Reset清理及再次申请。\n\n'
        '采用显式受控标签，不是在线预测。K=1使用独立元数据标签10，旧版本无法读取该临时文件系统；'
        '原库、原镜像和旧格式结果不覆盖。尚未进行RocksDB收益、正常并发或K1的实际ZC验证。\n')
    return result


def execute(args,password,output):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='固定K=1功能原型，独立元数据扩展，仅受控标签，无性能收益结论',GC启用=False)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
                    [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复原软件并在独立副本编译完整ZenFS，原库不覆盖')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_colocation_guest.py {run.target}',900)
        for tag in ('reference','disabled','enabled'):
            run.record(阶段=tag+'独立空盘功能验证')
            wait_stage(run,tag,'run_colocation_guest.sh',240,mode='greedy')
            run.record(已完成=[*run.state.get('已完成',[]),tag])
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_business(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
    proof=analyze(output)
    run.record(阶段='固定K1功能核验通过，已归档并正常关机',固定K1功能=proof)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-colocation-k1-v1-20261029')
    parser.add_argument('--baseline',type=Path,default=BASELINE)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admitted=admission(args.baseline);budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30,'新构建、归档及原磁盘保护余量不足')
    require(budget['可用内存字节']>=30<<30,'完整ZenFS独立编译及16GiB内存保护余量不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='完整ZenFS副本及对象编译；不重建静态库；保留原24GiB磁盘及16GiB内存余量')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('协同池原型认证（仅内存）：')
    output.mkdir();freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((args.baseline/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_colocation_guest.py','colocation_source.py',
                 'colocation_device.cc','build_all_blocks_guest.py','calibration_source.py','run_colocation_host.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    (output/'源码/受控标签.txt').write_text('/coloc/H.sst H\n/coloc/W.sst W\n/coloc/W2.sst W\n/coloc/H2.sst H\n')
    (output/'源码/run_colocation_guest.sh').write_text(GUEST)
    stamp(output/'预注册计划.json',dict(目的='固定K1真实功能及K0回归',基线=str(args.baseline),
        基线验收通过=admitted['通过'],平台=profile8(),实际顺序=['reference','disabled','enabled'],
        元数据边界='只在独立可销毁文件系统增加标签10；不能用旧版本挂载该原型盘',
        RR阈值=1024,GC主动执行=False,无模型收益结论=True,资源=budget))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
