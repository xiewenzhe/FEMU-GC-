#!/usr/bin/env bash

# 在已经格式化的隔离客机中执行；三个写入阶段共用同一 ZenFS 实例。
# 本脚本不格式化设备，不设置 GC 门槛；默认关闭 RR，仅 rr 模式开启。
set -euo pipefail

if [[ $# -lt 2 || $# -gt 6 ]]; then
  echo "用法：$0 条目数 日志标签 [SST目标字节数] [前台写速率字节每秒] [后台写速率字节每秒，0表示不限速] [模式：zc或rr，默认zc]" >&2
  exit 2
fi
if [[ ! $1 =~ ^[1-9][0-9]*$ || ! $2 =~ ^[a-zA-Z0-9_-]+$ ||
      ! ${3:-67108864} =~ ^[1-9][0-9]*$ ||
      ! ${4:-20971520} =~ ^[1-9][0-9]*$ ||
      ! ${5:-0} =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "条目数、SST 目标和前台写速率须为正整数，后台写速率可为 0；日志标签仅允许字母、数字、短横线和下划线。" >&2
  exit 2
fi
if [[ ${6:-zc} != zc && ${6:-zc} != rr ]]; then
  echo "模式仅允许 zc 或 rr；zc 关闭主机端 RR，rr 开启主机端 RR。" >&2
  exit 2
fi
if [[ $(id -u) -ne 0 ]]; then
  echo "请在隔离客机中以 root 身份运行。" >&2
  exit 2
fi

entries=$1
target_file_size=${3:-67108864}
write_rate=${4:-20971520}
background_write_rate=${5:-0}
mode=${6:-zc}
logdir=/tmp/rrzc-single-$2
if [[ -e $logdir ]]; then
  echo "日志目录已存在，停止以避免覆盖：$logdir" >&2
  exit 2
fi
cd /home/femu/rocksdb
test -x ./db_bench
mkdir "$logdir"
unset ZENFS_RR_ENABLE
if [[ $mode == rr ]]; then
  export ZENFS_RR_ENABLE=1
fi

echo "开始单进程预试：填充一次、覆盖写两次，每次 $entries 条，随后随机读取1000次。"
echo "实验模式：$mode；zc 关闭主机端 RR，rr 开启主机端 RR。"
echo "compaction 的 SST 目标为 $target_file_size 字节，memtable 为 67108864 字节。"
echo "前台写入限速为 $write_rate 字节每秒。"
echo "RocksDB 后台写入限速为 $background_write_rate 字节每秒，0 表示不限速；不限制 ZenFS 自身迁移。"
./db_bench \
  --fs_uri=zenfs://dev:nvme0n1 \
  '--benchmarks=fillseq,overwrite[X2],readrandom' \
  "--num=$entries" "--writes=$entries" --reads=1000 \
  --db=/rrzc-single-process \
  --value_size=4096 --compression_type=none --disable_wal=1 \
  --use_direct_io_for_flush_and_compaction=1 --use_direct_reads=1 \
  --write_buffer_size=67108864 "--target_file_size_base=$target_file_size" \
  --level0_file_num_compaction_trigger=2 \
  "--rate_limiter_bytes_per_sec=$background_write_rate" \
  "--benchmark_write_rate_limit=$write_rate" --seed=12345 \
  2>&1 | tee "$logdir/db_bench.stdout.log"

plugin/zenfs/util/zenfs df --zbd=nvme0n1 2>&1 | tee "$logdir/space.df.log"
echo "预试完成，原始输出：$logdir"
