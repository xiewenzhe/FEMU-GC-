#!/usr/bin/env bash
# 仅初始化本轮新子镜像中的全空实验命名空间，拒绝覆盖目录或复用非空设备。
set -euo pipefail
if [[ $# -ne 3 || $(id -u) -ne 0 ]]; then echo "需要root、全新结果目录、valid或weighted、周期数。" >&2; exit 2; fi
case "$2" in valid|weighted) ;; *) echo "策略非法。" >&2; exit 2;; esac
[[ $3 =~ ^[1-9][0-9]?$|^100$ ]] || { echo "周期数非法。" >&2; exit 2; }
[[ $1 == /tmp/rrzc-periodic-* && ! -e $1 && ! -e $1-aux ]] || { echo "目录非法或已存在。" >&2; exit 2; }
directory=/home/femu/rrzc-periodic-tests
program=$directory/periodic_rrzc_test
[[ -x $program && $(cat "$directory/build.exit") == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$1" "$1-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_weighted_selector.h librocksdb_debug.a "$program" "$directory/periodic_rrzc_test.cc" "$directory/run_isolated_periodic_guest.sh")
sha256sum "${identity[@]}" > "$1/identity.initial.sha256"
lscpu > "$1/cpu.log"
free -b > "$1/memory.initial.log"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$1/scheduler.log"
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
printf '策略,周期数,周期秒数,预热周期数,回收预算字节,冷热有效字节,每文件热身读数,每文件后续读数,延迟口径\n%s,%s,80,2,201326592,33554432/67108864,3584,768,单次ZenFS同步API耗时不含主动等待\n' "$2" "$3" > "$1/config.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=1 \
  ZENFS_RRZC_SIMPLE_MODE="$2" ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  "$program" run "$3" "$1" > "$1/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/run.exit"
if [[ $status == 0 ]]; then
  # 校验不参与性能和维护账目，关闭RR和简单选区入口。
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=0 \
    "$program" verify "$3" "$1" > "$1/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/verify.exit"
fi
printf '%s\n' "$status" > "$1/runner.exit"
sha256sum "${identity[@]}" > "$1/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.final.json"
free -b > "$1/memory.final.log"
find "$1" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$1/logs.sha256"
cat "$1/run.stdout.log"
[[ ! -e $1/verify.stdout.log ]] || cat "$1/verify.stdout.log"
exit "$status"
