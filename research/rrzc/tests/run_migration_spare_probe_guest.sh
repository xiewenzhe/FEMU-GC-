#!/usr/bin/env bash
# 只允许本轮全空8GiB隔离设备；不格式化、不操作旧镜像。
set -euo pipefail
target=/home/femu/rrzc-ycsb-retention-20261017-tests/migration-spare-20261019
[[ $# == 1 && ($1 == off || $1 == on) && $(id -u) == 0 ]]
mode=$1
[[ ! -e $target/probe-$mode.exit && $(cat "$target/build.exit") == 0 ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/probe-$mode.exit"; touch "$target/probe-$mode.finished"' EXIT
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
nvme zns report-zones /dev/nvme0n1 -o json > "$target/probe-$mode.zones.before.json"
python3 -c 'import json,sys;r=json.load(sys.stdin);z=r["zone_list"];assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 for v in z), "不是本轮全空设备"' < "$target/probe-$mode.zones.before.json"
enabled=0
[[ $mode != on ]] || enabled=1
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_ALLOC_DIAG=1 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_FALLBACK=0 \
  ZENFS_RRZC_MIGRATION_SPARE="$enabled" \
  "$target/migration_spare_probe" "$target/probe-$mode.zenfs.log" "$mode" > "$target/probe-$mode.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$target/probe-$mode.zones.after.json"
