#!/usr/bin/env bash

# 在已完成 ZenFS 格式化的隔离客机中运行；本脚本不会格式化或重置设备。
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != "zc" && "$1" != "rr" ) ]]; then
  echo "用法：$0 zc|rr" >&2
  exit 2
fi

if [[ $(id -u) -ne 0 ]]; then
  echo "请在隔离客机中以 root 身份运行。" >&2
  exit 2
fi

mode=$1
workdir=/home/femu/rocksdb
logdir=/tmp/rrzc-qualified-$mode

if [[ -e "$logdir" ]]; then
  echo "日志目录已存在，为避免覆盖原始结果而停止：$logdir" >&2
  exit 2
fi

cd "$workdir"
test -x ./db_bench
test -x plugin/zenfs/util/zenfs
mkdir -p "$logdir"
unset ZENFS_RR_ENABLE

# 两组使用完全相同的 RocksDB 参数；RR 只在最后一个阶段按需打开。
common=(
  --fs_uri=zenfs://dev:nvme0n1
  --value_size=4096
  --compression_type=none
  --use_direct_io_for_flush_and_compaction=1
  --disable_wal=1
  --write_buffer_size=67108864
  --target_file_size_base=67108864
  --level0_file_num_compaction_trigger=2
  --seed=12345
)

run_fill() {
  local label=$1 db=$2 count=$3 rate=$4 enable_rr=$5
  local -a command=(./db_bench "${common[@]}" --benchmarks=fillseq
    "--num=$count" "--db=$db" "--benchmark_write_rate_limit=$rate")

  echo "开始 $label：顺序填充 $count 次，主机端 RR=$enable_rr"
  if [[ $enable_rr == 1 ]]; then
    ZENFS_RR_ENABLE=1 "${command[@]}" 2>&1 | tee "$logdir/$label.stdout.log"
  else
    "${command[@]}" 2>&1 | tee "$logdir/$label.stdout.log"
  fi
}

run_overwrite() {
  local label=$1 db=$2 count=$3 writes=$4
  echo "开始 $label：复用数据库并覆盖写 $writes 次，主机端 RR=0"
  ./db_bench "${common[@]}" --benchmarks=overwrite --use_existing_db=1 \
    "--num=$count" "--writes=$writes" "--db=$db" \
    --benchmark_write_rate_limit=10485760 2>&1 | tee "$logdir/$label.stdout.log"
}

record_space() {
  local label=$1
  plugin/zenfs/util/zenfs df --zbd=nvme0n1 2>&1 | tee "$logdir/$label.df.log"
}

run_fill 01_hot_fill /rrzc-active8-hot 250000 52428800 0
run_overwrite 02_hot_overwrite /rrzc-active8-hot 250000 300000
record_space 02_hot_overwrite
run_fill 03_static_fill /rrzc-active8-static 450000 10485760 0
record_space 03_static_fill
run_overwrite 04_hot_overwrite /rrzc-active8-hot 250000 300000
record_space 04_hot_overwrite
run_fill 05_static2_fill /rrzc-active8-static2 400000 10485760 0
record_space 05_static2_fill

if [[ $mode == rr ]]; then
  run_fill 06_static3_fill /rrzc-active8-static3 100000 10485760 1
else
  run_fill 06_static3_fill /rrzc-active8-static3 100000 10485760 0
fi
record_space 06_static3_fill

echo "六阶段负载已完成；原始输出保存在 $logdir"
