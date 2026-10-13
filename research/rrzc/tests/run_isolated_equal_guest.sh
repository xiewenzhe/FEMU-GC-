#!/usr/bin/env bash
# 复用前轮隔离检查，仅全新本轮客机使用，禁止覆盖已有结果。
set -euo pipefail
if [[ $# -ne 2 || $(id -u) -ne 0 || $1 != /tmp/rrzc-equal-* || -e $1 || -e $1-aux || ($2 != smoke && $2 != slow && $2 != fast) ]]; then
  echo "隔离客机root专用：脚本 尚不存在的/tmp/rrzc-equal-目录 smoke|slow|fast；拒绝覆盖。" >&2
  exit 2
fi
program=/home/femu/rrzc-equal-tests/controlled_zc_test
[[ -x $program ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$1" "$1-aux"
lsblk -b -o NAME,SIZE,TYPE,MOUNTPOINTS > "$1/lsblk.initial.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-equal-tests/controlled_zc_test.cc > "$1/identity.initial.sha256"
sha256sum /home/femu/rrzc-equal-tests/run_isolated_equal_guest.sh > "$1/runner.sha256"
# 只设置本实验客机设备，与前轮保持一致。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
phases=("future-equal-$2")
if [[ $2 != smoke ]]; then phases+=(near-verify); fi
status=0
for phase in "${phases[@]}"; do
  enabled=1
  if [[ $phase == near-verify ]]; then enabled=0; fi
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE="$enabled" \
    "$program" "$phase" "$1" > "$1/$phase.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/$phase.exit"
  cat "$1/$phase.stdout.log"
  if [[ $status -ne 0 ]]; then break; fi
done
printf '%s\n' "$status" > "$1/runner.exit"
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-equal-tests/controlled_zc_test.cc > "$1/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.final.json"
# 保留失败阶段已经生成的日志，不更改失败退出码。
shopt -s nullglob
sha256sum "$1"/*.log "$1"/*.exit "$1"/*.csv "$1"/*.tsv "$1"/*.json "$1"/identity.*.sha256 "$1"/runner.sha256 > "$1/logs.sha256"
exit "$status"
