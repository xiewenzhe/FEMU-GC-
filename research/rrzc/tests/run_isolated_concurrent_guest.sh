#!/usr/bin/env bash
# 只用于本轮新建的隔离客机，执行前须核对其 QEMU 子镜像和端口。
# 确认8GiB/32个全空未挂载区后初始化ZenFS，不适用于原始镜像或已有数据设备。
set -euo pipefail
if [[ $# -ne 2 || $(id -u) -ne 0 || $1 != /tmp/rrzc-concurrent-* || -e $1 || -e $1-aux || ($2 != smoke && $2 != full) ]]; then
  echo "仅隔离客机root可用：脚本 尚不存在的/tmp/rrzc-concurrent-目录 smoke|full；拒绝覆盖。" >&2
  exit 2
fi
program=/home/femu/rrzc-controlled-concurrent-tests/controlled_zc_test
runner=/home/femu/rrzc-controlled-concurrent-tests/run_controlled_concurrent.sh
[[ -x $program && -f $runner ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
if [[ -n $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]; then
  echo "目标ZNS已挂载，拒绝初始化。" >&2
  exit 2
fi
mkdir "$1" "$1-aux"
lsblk -b -o NAME,SIZE,TYPE,MOUNTPOINTS > "$1/lsblk.initial.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-controlled-concurrent-tests/controlled_zc_test.cc > "$1/identity.initial.sha256"
sha256sum "$runner" /home/femu/rrzc-controlled-concurrent-tests/run_isolated_concurrent_guest.sh > "$1/runner.sha256"
# 仅本隔离客机的实验设备调度器，与前一轮保持一致，不修改宿主配置。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
status=0
if [[ $2 == smoke ]]; then
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=1 \
    "$program" concurrent-smoke "$1" > "$1/concurrent-smoke.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/concurrent-smoke.exit"
  cat "$1/concurrent-smoke.stdout.log"
else
  bash "$runner" "$1" "$program" > "$1/runner.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/runner.exit"
  cat "$1/runner.stdout.log"
fi
sha256sum db_bench plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc "$program" /home/femu/rrzc-controlled-concurrent-tests/controlled_zc_test.cc > "$1/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.final.json"
sha256sum "$1"/*.log "$1"/*.exit "$1"/*.csv "$1"/*.tsv "$1"/*.json "$1"/identity.*.sha256 "$1"/runner.sha256 > "$1/logs.sha256"
exit "$status"
