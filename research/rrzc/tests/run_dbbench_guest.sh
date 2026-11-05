#!/usr/bin/env bash
# 独立全空设备；两种策略只改变选区评分，不修改RR阈值或GC条件。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 5 && $(id -u) == 0 ]]
tag=$1; mode=$2; case_name=$3; records=$4; operations=$5
[[ $tag =~ ^(smoke|full)-(hot|mixed95|mixed50)-(space|model)$ ]]
[[ $mode == space || $mode == model ]]
[[ $case_name == hot || $case_name == mixed95 || $case_name == mixed50 ]]
[[ $records =~ ^[0-9]+$ && $operations =~ ^[0-9]+$ ]]
result=$target/$tag
[[ ! -e $result && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==64 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]*512==134217728 for v in z)' < "$result/zones.initial.json"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
probe=/home/femu/joint-ycsb32-af-v3-20261023/quick_device_probe
"$probe" "$result/probe.initial.log" > "$result/device.initial.json"
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
"$probe" "$result/probe.before.log" > "$result/device.before.json"
enabled=0; greedy=1
if [[ $mode == model ]]; then enabled=1; greedy=0; fi
printf '策略=%s\nRR阈值=1024\nGC启动百分比=20\n边界开关=0\nRR评分=%s\n' "$mode" "$enabled" > "$result/策略.txt"
common=(--fs_uri=zenfs://dev:nvme0n1 --db=/rrzc-dbbench "--num=$records" --value_size=4096
  --compression_type=none --disable_wal=1 --cache_size=0 --row_cache_size=0
  --use_direct_reads=1 --use_direct_io_for_flush_and_compaction=1 --verify_checksum=1
  --write_buffer_size=16777216 --max_write_buffer_number=3 --max_background_jobs=2
  --target_file_size_base=16777216 --max_bytes_for_level_base=134217728
  --level0_file_num_compaction_trigger=2 --level0_slowdown_writes_trigger=4
  --level0_stop_writes_trigger=8 --bloom_bits=10 --histogram=1 --threads=1
  --stats_interval=1000 --stats_interval_seconds=10 --seed=20261023
  --benchmark_write_rate_limit=0 --rate_limiter_bytes_per_sec=0)
printf '%q ' "${common[@]}" > "$result/公共参数.txt"
run_bench() {
  # 环境列表由已验收去边界入口生成；默认库和旧实验入口不变。
  __DBBENCH_ENV__
    DBBENCH_PID_FILE="$result/bench.pid" \
    python3 -c 'import os,resource,sys;from pathlib import Path;resource.setrlimit(resource.RLIMIT_FSIZE,(512<<20,512<<20));Path(os.environ["DBBENCH_PID_FILE"]).write_text(str(os.getpid()));os.execv(sys.argv[1],sys.argv[1:])' \
    ./db_bench "${common[@]}" "$@"
}
seal_log() {
  python3 - "$result" "$1" <<'PY'
from pathlib import Path
import os,sys
r=Path(sys.argv[1]);phase=sys.argv[2]
old=set((r/(phase+'.logs.before.txt')).read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'无法唯一定位本阶段维护日志'
os.link(logs[0],r/(phase+'.zenfs.log'))
rock=r/'aux/rrzc-dbbench/LOG'
assert rock.is_file(),'缺少本阶段RocksDB日志'
os.link(rock,r/(phase+'.rocksdb.log'))
PY
}
printf 'load\n' > "$result/phase.txt"
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/load.logs.before.txt"
run_bench --benchmarks=fillseq,flush,waitforcompaction "--writes=$records" > "$result/load.log" 2>&1
seal_log load
printf '0\n' > "$result/load.exit"
printf 'run\n' > "$result/phase.txt"
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/run.logs.before.txt"
if [[ $case_name == hot ]]; then
  writes=150000
  [[ $tag != smoke-* ]] || writes=2048
  business=(--benchmarks=readwhilewriting "--reads=$operations" "--writes=$writes"
    --read_random_exp_range=10 --finish_after_writes=1)
else
  percent=95
  [[ $case_name != mixed50 ]] || percent=50
  business=(--benchmarks=readrandomwriterandom "--reads=$operations" --writes=0 "--readwritepercent=$percent")
fi
printf '%q ' "${business[@]}" > "$result/业务参数.txt"
python3 -c 'import time;print(time.monotonic_ns())' > "$result/run.start.ns"
run_bench --use_existing_db=1 "${business[@]}" > "$result/run.log" 2>&1
python3 -c 'import time;print(time.monotonic_ns())' > "$result/run.end.ns"
seal_log run
printf '0\n' > "$result/run.exit"
"$probe" "$result/probe.after.log" > "$result/device.after.json"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
python3 "$target/strict_stats_guest.py" > "$result/strict.business.json"
printf 'verify\n' > "$result/phase.txt"
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/verify.logs.before.txt"
run_bench --use_existing_db=1 --benchmarks=readseq,verifychecksum "--reads=$records" --writes=0 > "$result/verify.log" 2>&1
seal_log verify
printf '0\n' > "$result/verify.exit"
python3 "$target/strict_stats_guest.py" > "$result/strict.after-verify.json"
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
python3 "$target/capture_dbbench_guest.py" progress "$result" > "$result/最终观测.json"
printf 'done\n' > "$result/phase.txt"
find "$result" -type f ! -name logs.sha256 ! -name 增量观测.json ! -name 最终观测.json -print0 | sort -z | xargs -0 sha256sum > "$result/logs.sha256"
