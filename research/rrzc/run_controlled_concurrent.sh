#!/usr/bin/env bash
# 已初始化的隔离客机专用：RR/ZC在一个观察进程始终开启，校验另进程关闭RR。
# 不格式化、不改调度/GC/设备参数；准备及固定补读仍使用测试文件锁同步。
set -euo pipefail
if [[ $# -ne 2 || $1 != /tmp/rrzc-* || ! -d $1 || $2 != /* || ! -x $2 || $(id -u) -ne 0 ]]; then
  echo "用法：脚本 已存在的/tmp/rrzc-独立目录 测试程序绝对路径；须在隔离客机以root运行。" >&2
  exit 2
fi
for phase in concurrent verify; do
  if [[ -e $1/$phase.stdout.log || -e $1/$phase.exit || -e $1/zenfs.$phase.log ]]; then
    echo "已有阶段结果，拒绝覆盖：$phase" >&2
    exit 2
  fi
done
for phase in concurrent verify; do
  enabled=0
  if [[ $phase == concurrent ]]; then enabled=1; fi
  status=0
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
    ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE="$enabled" \
    "$2" "$phase" "$1" > "$1/$phase.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/$phase.exit"
  cat "$1/$phase.stdout.log"
  if [[ $status -ne 0 ]]; then
    echo "阶段失败：$phase，退出$status；保留原始结果，停止后续阶段。" >&2
    exit "$status"
  fi
done
echo "同进程同时维护观察完成；测试同步场景，不是性能对照或选区收益证明。"
