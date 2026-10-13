#!/usr/bin/env bash
# 只对本轮全新隔离子镜像使用；先查空区，结果目录禁止覆盖。
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "需要新隔离目录与模式，拒绝缺参数执行。" >&2
  exit 2
fi
directory=/home/femu/rrzc-choice-tests
prefix=/tmp/rrzc-choice-
verify_phase=choice-verify
case "$2" in
  smoke|cold|hot) ;;
  expire-smoke|expire-cold|expire-hot)
    directory=/home/femu/rrzc-expire-tests
    prefix=/tmp/rrzc-expire-
    verify_phase=choice-expire-verify ;;
  *) echo "实验模式非法，拒绝执行。" >&2; exit 2 ;;
esac
if [[ $(id -u) -ne 0 || $1 != "$prefix"* || -e $1 || -e $1-aux ]]; then
  echo "仅隔离客机root与相应全新目录允许执行，拒绝覆盖。" >&2
  exit 2
fi
program=$directory/controlled_zc_test
[[ -x $program && $(cat "$directory/build.exit") == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$1" "$1-aux"
lsblk -b -o NAME,SIZE,TYPE,MOUNTPOINTS > "$1/lsblk.initial.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
identity=(db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" "$directory/controlled_zc_test.cc" librocksdb_debug.a)
sha256sum "${identity[@]}" > "$1/identity.initial.sha256"
sha256sum "$directory/run_isolated_choice_guest.sh" > "$1/runner.sha256"
# 仅设置实验客机设备，调度器与此前保持一致。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
source_zone=7784628224
if [[ ${2#expire-} == cold ]]; then source_zone=7516192768; fi
phases=("choice-$2")
if [[ ${2#expire-} != smoke ]]; then phases+=("$verify_phase"); fi
for phase in "${phases[@]}"; do
  enabled=1
  if [[ $phase == "$verify_phase" ]]; then enabled=0; fi
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
    ZENFS_RR_ENABLE="$enabled" ZENFS_ZC_TEST_ENABLE="$enabled" ZENFS_ZC_TEST_SOURCE="$source_zone" \
    "$program" "$phase" "$1" > "$1/$phase.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/$phase.exit"
  cat "$1/$phase.stdout.log"
  if [[ $status -ne 0 ]]; then break; fi
done
printf '%s\n' "$status" > "$1/runner.exit"
sha256sum "${identity[@]}" > "$1/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.final.json"
shopt -s nullglob
sha256sum "$1"/*.log "$1"/*.exit "$1"/*.csv "$1"/*.json "$1"/identity.*.sha256 "$1"/runner.sha256 > "$1/logs.sha256"
exit "$status"
