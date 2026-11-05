#!/usr/bin/env bash
# 每臂先重置本次独立可销毁FEMU盘；串行重建真实NAND、文件和读历史。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 2 && $(id -u) == 0 && $(blockdev --getsize64 /dev/nvme0n1) == 4294967296 ]]
scenario=$1
mode=$2
[[ $scenario == opportunity || $scenario == stopped || $scenario == low-yield ]]
[[ $mode == greedy || $mode == current || $mode == blocks ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
result=$target/$scenario-$mode
[[ ! -e $result ]]
mkdir "$result" "$result/aux"
trap 'code=$?; echo "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
# 这里只操作宿主新建的独立模拟盘；不能对用户真实盘使用此包装器。
nvme zns reset-zone /dev/nvme0n1 -a > "$result/初始化Reset.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 - "$result/zones.initial.json" <<'PY'
import json,sys
z=json.load(open(sys.argv[1]))['zone_list']
assert len(z)==32 and all(r['state']=='EMPTY' and r['wp']==r['slba'] for r in z)
PY
cd /home/femu/rocksdb
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
greedy=0
blocks=0
[[ $mode != greedy ]] || greedy=1
[[ $mode != blocks ]] || blocks=1
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 ZENFS_RRZC_GUARDED_MODE=shadow \
  ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 ZENFS_RRZC_MIGRATION_WINDOW=1 \
  ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 ZENFS_RRZC_SINGLE_RECOVERY=1 \
  ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_EXACT_LIVE=1 \
  ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 ZENFS_RRZC_MIGRATION_SPARE=1 \
  ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_RR_ENABLE=1 ZENFS_RR_STRICT=1 ZENFS_RR_FAIR_QUEUE=1 ZENFS_RRZC_DIRECT=1 \
  ZENFS_RRZC_ETA_OBSERVE=1 ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_STABLE_CREDIT=0 \
  ZENFS_RRZC_PAIR_GREEDY="$greedy" ZENFS_RRZC_WINDOW_CREDIT=1 \
  ZENFS_RRZC_PERSISTENT_CREDIT=1 ZENFS_RRZC_EXPECTED_RR=1 \
  ZENFS_RRZC_CANDIDATE_POOL=expanded ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 \
  ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0 ZENFS_RRZC_ALL_BLOCKS="$blocks" \
  "$target/all_blocks_device" "$scenario" "$result" > "$result/run.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
