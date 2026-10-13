#!/usr/bin/env bash
# 仅在已初始化的隔离客机中执行；不格式化设备、不调整 GC 或设备阈值。
# 准备、RR、完整字节校验分进程，校验读不混入受控读取负载。
set -euo pipefail
if [[ $# -ne 3 || ! $1 =~ ^(256|1024|4096)$ || $2 != /tmp/rrzc-* || ! -d $2 || ! -x $3 ]]; then
  echo "用法：脚本 256|1024|4096 已存在的/tmp/rrzc-独立目录 测试程序绝对路径" >&2
  exit 2
fi
if [[ $(id -u) -ne 0 || $3 != /* ]]; then
  echo "须在隔离客机以 root 身份运行，并提供程序绝对路径。" >&2
  exit 2
fi
for phase in prepare rr verify; do
  if [[ -e $2/$phase.stdout.log || -e $2/$phase.exit || -e $2/zenfs.$phase.log ]]; then
    echo "阶段结果已存在，拒绝覆盖：$phase" >&2
    exit 2
  fi
done
for phase in prepare rr verify; do
  rr_enabled=0
  if [[ $phase == rr ]]; then rr_enabled=1; fi
  phase_status=0
  env ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
    ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE="$rr_enabled" \
    "$3" "$phase" "$1" "$2" > "$2/$phase.stdout.log" 2>&1 || phase_status=$?
  printf '%s\n' "$phase_status" > "$2/$phase.exit"
  cat "$2/$phase.stdout.log"
  if [[ $phase_status -ne 0 ]]; then
    echo "阶段失败：$phase，退出 $phase_status；保留结果，不执行后续阶段。" >&2
    exit "$phase_status"
  fi
done
echo "三阶段完成；这只是受控功能与成本观察，不是最终阈值校准或性能收益。"
