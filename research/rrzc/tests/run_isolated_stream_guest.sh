#!/usr/bin/env bash
# 只在本轮新隔离子镜像的完整空命名空间初始化；拒绝覆盖目录。
set -euo pipefail
[[ ( $# == 3 || $# == 4 ) && $(id -u) == 0 ]] || { echo "需要root、新目录、策略、秒数和可选空间权重。" >&2; exit 2; }
space_weight=${4:-50}
[[ $space_weight == 50 || $space_weight == 80 ]] || { echo "当前实验仅支持空间权重50或80。" >&2; exit 2; }
case "$2" in valid|weighted) ;; *) echo "策略非法。" >&2; exit 2;; esac
[[ $1 =~ ^/home/femu/rrzc-stream-results/[a-z0-9-]+$ && ! -e $1 && ! -e $1-aux ]] || { echo "目录非法或已存在。" >&2; exit 2; }
[[ $3 =~ ^[1-9][0-9]{1,4}$ ]] && (( $3 >= 60 && $3 <= 14400 && $3 % 30 == 0 )) || { echo "时长非法。" >&2; exit 2; }
directory=/home/femu/rrzc-stream-tests
program=$directory/stream_rrzc_test
[[ -x $program && $(cat "$directory/build.exit") == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-stream-results
mkdir "$1" "$1-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$1/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$1/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_weighted_selector.h librocksdb_debug.a "$program" "$directory/stream_rrzc_test.cc" "$directory/run_isolated_stream_guest.sh")
sha256sum "${identity[@]}" > "$1/identity.initial.sha256"
lscpu > "$1/cpu.log"
free -b > "$1/memory.initial.log"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$1/scheduler.log"
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$1-aux" --enable_gc > "$1/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/mkfs.exit"
[[ $status == 0 ]]
warmup=600
if (( $3 < 1200 )); then warmup=60; fi
printf '策略,计划业务秒数,预热秒数,代际间隔秒数,读热点寿命秒数,填充Zone数,回收预算字节,RR阈值,读写是否并发,评分空间权重百分比,评分读风险权重百分比\n%s,%s,%s,30,120,20,201326592,4096,1,%s,%s\n' "$2" "$3" "$warmup" "$space_weight" "$((100-space_weight))" > "$1/config.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=1 \
  ZENFS_RRZC_SIMPLE_MODE="$2" ZENFS_RRZC_RECLAIM_BYTES=201326592 ZENFS_RRZC_SPACE_WEIGHT_PCT="$space_weight" \
  "$program" run "$3" "$1" > "$1/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$1/run.exit"
if [[ $status == 0 ]]; then
  # 真正只读挂载，不启动维护线程，校验读取不计入业务统计。
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
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
