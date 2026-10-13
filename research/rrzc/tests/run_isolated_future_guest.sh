#!/usr/bin/env bash
# 仅已核实身份的本轮全新隔离子镜像使用；全空检查后才初始化实验设备。
set -euo pipefail
if [[ $# -ne 2 || $(id -u) -ne 0 || $1 != /tmp/rrzc-future-* || -e $1 || -e $1-aux || ($2 != smoke && $2 != low && $2 != medium && $2 != high) ]]; then
  echo "隔离客机root专用：脚本 尚不存在的/tmp/rrzc-future-目录 smoke|low|medium|high；拒绝覆盖。" >&2
  exit 2
fi
program=/home/femu/rrzc-future-tests/controlled_zc_test
[[ -x $program ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$1" "$1-aux"
lsblk -b -o NAME,SIZE,TYPE,MOUNTPOINTS > "$1/lsblk.initial.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-future-tests/controlled_zc_test.cc > "$1/identity.initial.sha256"
sha256sum /home/femu/rrzc-future-tests/run_isolated_future_guest.sh > "$1/runner.sha256"
# 仅本实验客机的设备，与前轮保持一致，不修改宿主配置。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
phases=("future-$2")
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
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-future-tests/controlled_zc_test.cc > "$1/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.final.json"
# 失败时部分CSV可能尚未生成，仍归档已经存在的日志，不覆盖原失败退出码。
shopt -s nullglob
sha256sum "$1"/*.log "$1"/*.exit "$1"/*.csv "$1"/*.tsv "$1"/*.json "$1"/identity.*.sha256 "$1"/runner.sha256 > "$1/logs.sha256"
exit "$status"
