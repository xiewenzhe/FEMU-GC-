#!/usr/bin/env bash
# 新旧模型同库同负载，唯一策略差异为边界估价；保持原严格RR和GC条件。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 2 && $(id -u) == 0 && ( $1 == old || $1 == terminal ) && $2 == retire ]]
mode=$1
scenario=$2
enabled=0
[[ $mode == old ]] || enabled=1
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
printf '策略=%s\n候选范围=expanded\nRR成本开关=1\n时间一致开关=1\n边界开关=%s\n' "$mode" "$enabled" > "$result/策略.txt"
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
  ZENFS_RRZC_PAIR_GREEDY=0 ZENFS_RRZC_WINDOW_CREDIT=1 ZENFS_RRZC_PERSISTENT_CREDIT=1 ZENFS_RRZC_EXPECTED_RR=1 \
  ZENFS_RRZC_CANDIDATE_POOL=expanded ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 \
  ZENFS_RRZC_TERMINAL="$enabled" \
  "$target/joint_time_smoke" run "$scenario" "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
python3 "$target/strict_stats_guest.py" > "$result/strict.business.json"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=1 ZENFS_RR_STRICT=1 ZENFS_RR_STRICT_AUDIT=1 \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_MIGRATE_RESERVE=1 \
    ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
    "$target/joint_time_smoke" verify "$scenario" "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
python3 "$target/strict_stats_guest.py" > "$result/strict.after-verify.json"
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
exit "$status"
