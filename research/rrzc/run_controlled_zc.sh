#!/usr/bin/env bash
# 已初始化的隔离客机专用；不格式化、不改 GC/设备参数。
# ZC、已有风险 RR、固定未来读、后续 RR、完整校验分进程记录。
set -euo pipefail
if [[ $# -ne 2 || $1 != /tmp/rrzc-* || ! -d $1 || $2 != /* || ! -x $2 || $(id -u) -ne 0 ]]; then
  echo "用法：脚本 已存在的/tmp/rrzc-独立目录 测试程序绝对路径；须在隔离客机以root运行。" >&2
  exit 2
fi
for phase in zc rr-first future rr-second verify; do
  if [[ -e $1/$phase.stdout.log || -e $1/$phase.exit || -e $1/zenfs.$phase.log ]]; then
    echo "已有阶段结果，拒绝覆盖：$phase" >&2
    exit 2
  fi
done
for phase in zc rr-first future rr-second verify; do
  enabled=0
  if [[ $phase == rr-first || $phase == rr-second ]]; then enabled=1; fi
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
echo "四格分阶段观察完成；不是同时运行的性能对照或最终选区收益证明。"
