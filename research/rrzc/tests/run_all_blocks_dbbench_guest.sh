#!/usr/bin/env bash
# 真实RocksDB SST轮转负载；同一隔离客机逐臂重新初始化8GiB模拟盘。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 1 && $(id -u) == 0 && ( $1 == greedy || $1 == blocks ) ]]
mode=$1
result=$target/$mode
[[ ! -e $result && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$result" "$result/aux"
trap 'code=$?; echo "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
nvme zns reset-zone /dev/nvme0n1 -a > "$result/初始化Reset.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 - "$result/zones.initial.json" <<'PY'
import json,sys
z=json.load(open(sys.argv[1]))['zone_list']
assert len(z)==64 and all(r['state']=='EMPTY' and r['wp']==r['slba'] and r['cap']*512==134217728 for r in z)
PY
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cd /home/femu/rocksdb
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
enabled=0;greedy=1;planning=()
if [[ $mode == blocks ]]; then
  enabled=1;greedy=0
  planning=(ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0)
fi
common=(--fs_uri=zenfs://dev:nvme0n1 --db=/rrzc-dbbench --num=1048576 --value_size=4096
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
  env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
    ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
    ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
    ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
    ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
    ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 \
    ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_RRZC_DIRECT=1 \
    ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_ETA_OBSERVE=1 ZENFS_RR_FAIR_QUEUE=1 \
    ZENFS_RRZC_PAIR_GREEDY="$greedy" ZENFS_RRZC_WINDOW_CREDIT="$enabled" ZENFS_RRZC_STABLE_CREDIT=0 \
    ZENFS_RRZC_PERSISTENT_CREDIT="$enabled" ZENFS_RRZC_EXPECTED_RR="$enabled" \
    ZENFS_RRZC_CANDIDATE_POOL=expanded ZENFS_RRZC_ALL_BLOCKS="$enabled" "${planning[@]}" \
    RRZC_ROTATE_ROUNDS=24 RRZC_ROTATE_PHASE_BATCHES=4 RRZC_DB_HOTSPOT="$hotspot" \
    RRZC_DB_READ_KEYS=4096 RRZC_DB_WRITE_KEYS=61440 RRZC_DB_WRITE_HOT_PERCENT=80 \
    RRZC_DB_LAYOUT_PREFIX="$result/布局" DBBENCH_PID_FILE="$result/bench.pid" \
    python3 -c 'import os,resource,sys;from pathlib import Path;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));Path(os.environ["DBBENCH_PID_FILE"]).write_text(str(os.getpid()));os.execv(sys.argv[1],sys.argv[1:])' \
    "$target/db_bench" "${common[@]}" "$@"
}
seal_log() {
  python3 - "$result" "$1" <<'PY'
from pathlib import Path
import os,sys
r=Path(sys.argv[1]);phase=sys.argv[2]
old=set((r/(phase+'.logs.before.txt')).read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'无法唯一定位阶段ZenFS日志'
os.link(logs[0],r/(phase+'.zenfs.log'))
p=r/'aux/rrzc-dbbench/LOG';assert p.is_file(),'缺少RocksDB日志'
os.link(p,r/(phase+'.rocksdb.log'))
PY
}
snapshot() {
  nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.$1.json"
  if [[ $1 == before ]]; then
    env -i PATH=/usr/bin:/bin ZENFS_RR_STRICT_AUDIT=1 "$target/space_probe" "$result/空间探针.$1.log" > "$result/space.$1.json"
  fi
  python3 "$target/strict_stats_guest.py" > "$result/strict.$1.json"
  if [[ $1 != before ]]; then
    env -i PATH=/usr/bin:/bin ZENFS_RR_STRICT_AUDIT=1 "$target/space_probe" "$result/空间探针.$1.log" > "$result/space.$1.json"
  fi
}
phase() {
  printf '%s\n' "$1" > "$result/phase.txt"
  find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/$1.logs.before.txt"
}
hotspot=0;phase load
run_bench --benchmarks=fillseq,flush,waitforcompaction --writes=1048576 > "$result/load.log" 2>&1
seal_log load;snapshot before
hotspot=1;phase run
python3 -c 'import time;print(time.monotonic_ns())' > "$result/run.start.ns"
run_bench --use_existing_db=1 --benchmarks=rrzclayout,rrzcrotating,flush,waitforcompaction,rrzclayout \
  --reads=786432 --writes=589824 > "$result/run.log" 2>&1
python3 -c 'import time;print(time.monotonic_ns())' > "$result/run.end.ns"
seal_log run;snapshot business
hotspot=0;phase verify
run_bench --use_existing_db=1 --benchmarks=readseq,verifychecksum --reads=1048576 --writes=0 > "$result/verify.log" 2>&1
seal_log verify;snapshot verify
printf 'done\n' > "$result/phase.txt"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$result/logs.sha256"
