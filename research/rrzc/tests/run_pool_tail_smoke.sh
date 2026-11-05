#!/usr/bin/env bash
# 仅在本轮独立全空模拟设备做一次安全检查；不作性能或收益依据。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
result=$target/tail-smoke
[[ $(id -u) == 0 && ! -e $result && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ $(cat /sys/class/nvme/nvme0/model) == *FEMU* ]]
# 与完整业务入口相同的客机设备准备；不修改宿主调度器。
echo mq-deadline > /sys/block/nvme0n1/queue/scheduler
[[ $(cat /sys/block/nvme0n1/queue/scheduler) == *'[mq-deadline]'* ]]
mkdir "$result" "$result/aux"
cd /home/femu/rocksdb
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" > "$result/mkfs.log" 2>&1
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 \
  ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RR_FAIR_QUEUE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
  ZENFS_RRZC_POOL_K=1 ZENFS_RRZC_STREAM_CLASSIFY=1 ZENFS_RRZC_POOL_FLUSH_W=1 \
  ZENFS_RRZC_POOL_TAIL_OBSERVE=1 ZENFS_RRZC_POOL_TAIL_REUSE=1 \
  "$target/pool_tail_device" "$result" > "$result/run.log" 2>&1
if grep -Fq '[ERROR]' "$result/zenfs.log"; then
  echo '尾部安全检查存在ZenFS错误，请检查原始日志' >&2
  exit 1
fi
