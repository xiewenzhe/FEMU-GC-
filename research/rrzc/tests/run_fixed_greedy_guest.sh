#!/usr/bin/env bash
# 补齐同版本Greedy；影子采样与原入口一致，不执行风险改选。
set -euo pipefail
[[ $# == 0 && $(id -u) == 0 ]]
directory=/home/femu/rrzc-guarded-medium-20261015-greedy-tests
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
env -u ZENFS_RRZC_TAIL_ABLATION bash ./run_guarded_stream_guest.sh greedy original medium
