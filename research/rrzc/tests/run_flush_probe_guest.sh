#!/usr/bin/env bash
# 只在本轮隔离8GiB命名空间检查内存编码；不写用户数据、不格式化设备。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $(id -u) == 0 && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
env ZENFS_RRZC_POOL_K=1 ZENFS_RRZC_POOL_TAGS="$target/受控标签.txt" \
  ZENFS_MIGRATE_RESERVE=1 ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
  "$target/flush_metadata_probe" "$target/来源元数据探针.zenfs.log" > "$target/来源元数据探针.log" 2>&1
