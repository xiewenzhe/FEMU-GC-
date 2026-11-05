#!/usr/bin/env bash
# 仅新空设备功能短测；同一文件载荷验证统一模型真正选择热区。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
result=/home/femu/rrzc-benefit-device-results/benefit
[[ ( $# == 0 || ( $# == 1 && ( $1 == spare || $1 == content ) ) ) && $(id -u) == 0 && ! -e $result && $(cat "$target/build.exit") == 0 ]]
program=benefit_device_smoke
extra=()
if [[ $# == 1 && $1 == spare ]]; then program=direct_spare_smoke; fi
if [[ $# == 1 && $1 == content ]]; then program=content_guard_smoke; extra=(ZENFS_RRZC_CONTENT_GUARD=1); fi
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 && -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-benefit-device-results
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==32 and all(v["state"]=="EMPTY" for v in z)' < "$result/zones.initial.json"
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
status=0
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 ZENFS_RRZC_MIGRATION_SPARE=1 \
  ZENFS_RRZC_DIRECT=1 "${extra[@]}" "$target/$program" run benefit "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=0 "${extra[@]}" \
    "$target/$program" verify benefit "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
printf '%s\n' "$status" > "$result/runner.exit"
exit "$status"
