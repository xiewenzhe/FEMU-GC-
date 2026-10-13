#!/usr/bin/env bash
# 已初始化隔离客机专用；同进程开启RR/ZC，只改变测试数据失效时序。
set -euo pipefail
if [[ $# -ne 3 || $(id -u) -ne 0 || $1 != /tmp/rrzc-near-* || ! -d $1 || $2 != /* || ! -x $2 || ($3 != before && $3 != after) ]]; then
  echo "用法：脚本 已存在的/tmp/rrzc-near-目录 测试程序绝对路径 before|after；隔离客机root专用。" >&2
  exit 2
fi
for phase in "near-$3" near-verify; do
  if [[ -e $1/$phase.stdout.log || -e $1/$phase.exit || -e $1/zenfs.$phase.log ]]; then
    echo "已有结果，拒绝覆盖：$phase" >&2
    exit 2
  fi
done
for phase in "near-$3" near-verify; do
  enabled=1
  if [[ $phase == near-verify ]]; then enabled=0; fi
  status=0
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE="$enabled" \
    "$2" "$phase" "$1" > "$1/$phase.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$1/$phase.exit"
  cat "$1/$phase.stdout.log"
  if [[ $status -ne 0 ]]; then
    echo "阶段失败：$phase，退出$status；停止后续阶段并保留结果。" >&2
    exit "$status"
  fi
done
echo "近阈值/失效时序功能观察完成；不是RR-aware策略收益或性能对照。"
