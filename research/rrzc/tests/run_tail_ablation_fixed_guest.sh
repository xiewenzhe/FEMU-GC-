#!/usr/bin/env bash
# 独立包装原900秒脚本；认证会话退出不会挂断业务，完成标志晚于日志写完。
set -euo pipefail
[[ $# == 1 && ( $1 == keep || $1 == bypass ) && $(id -u) == 0 ]]
mode=$1
directory=/home/femu/rrzc-guarded-medium-20261015-guarded-tests
cd "$directory"
[[ ! -e launcher.exit && ! -e finished ]]
finish() {
  status=$?
  printf '%s\n' "$status" > launcher.exit
  touch finished
}
trap finish EXIT
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
env -u ZENFS_MIGRATE_RESERVE ./cost_device_probe "$directory/probe-device.log" > probe.csv
env ZENFS_RRZC_TAIL_ABLATION="$mode" bash ./run_guarded_stream_guest.sh guarded original medium
