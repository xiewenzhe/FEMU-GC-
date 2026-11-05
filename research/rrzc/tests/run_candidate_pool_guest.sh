#!/usr/bin/env bash
# 三组仅改变候选范围和评分分支；固定真实读取，业务与校验维护分账。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 2 && $(id -u) == 0 ]]
mode=$1
scenario=$2
case $mode in
  original-greedy) pool=original; greedy=1; enabled=0;;
  expanded-greedy) pool=expanded; greedy=1; enabled=0;;
  expanded-expected) pool=expanded; greedy=0; enabled=1;;
  *) exit 2;;
esac
[[ $scenario == opportunity || $scenario == low-yield || $scenario == stopped ]]
result=/home/femu/rrzc-joint-results/$scenario
[[ ! -e $result && $(cat "$target/build.exit") == 0 &&
   $(blockdev --getsize64 /dev/nvme0n1) == 4294967296 &&
   -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-joint-results
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]*512==134217728 for v in z)' < "$result/zones.initial.json"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
printf '策略=%s\n候选范围=%s\nGreedy开关=%s\n模型开关=%s\n严格RR=1\n' \
  "$mode" "$pool" "$greedy" "$enabled" > "$result/策略.txt"
status=0
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 RRZC_JOINT_TRACE=1 \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 ZENFS_RRZC_GUARDED_MODE=shadow \
  ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 ZENFS_RRZC_MIGRATION_WINDOW=1 \
  ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 ZENFS_RRZC_SINGLE_RECOVERY=1 \
  ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_EXACT_LIVE=1 \
  ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 ZENFS_RRZC_MIGRATION_SPARE=1 \
  ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_RR_ENABLE=1 ZENFS_RR_STRICT=1 ZENFS_RR_FAIR_QUEUE=1 ZENFS_RRZC_DIRECT=1 \
  ZENFS_RRZC_ETA_OBSERVE=1 ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_STABLE_CREDIT=0 \
  ZENFS_RRZC_PAIR_GREEDY="$greedy" ZENFS_RRZC_WINDOW_CREDIT="$enabled" \
  ZENFS_RRZC_PERSISTENT_CREDIT="$enabled" ZENFS_RRZC_EXPECTED_RR="$enabled" \
  ZENFS_RRZC_CANDIDATE_POOL="$pool" \
  "$target/candidate_pool_smoke" run "$scenario" "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
python3 "$target/strict_stats_guest.py" > "$result/strict.business.json"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=1 ZENFS_RR_STRICT=1 ZENFS_RR_STRICT_AUDIT=1 \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_MIGRATE_RESERVE=1 \
    ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
    "$target/candidate_pool_smoke" verify "$scenario" "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
python3 "$target/strict_stats_guest.py" > "$result/strict.after-verify.json"
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
exit "$status"
