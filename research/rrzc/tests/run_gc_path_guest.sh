#!/usr/bin/env bash
# 独立可销毁设备的GC路径教学实验；RR三条执行路径均关闭，保留原20%触发。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -u) == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 && ! -e $target/gc ]]
result=$target/gc
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
cd /home/femu/rocksdb
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 - "$result/zones.initial.json" <<'PY'
import json,sys
z=json.load(open(sys.argv[1]))['zone_list']
assert len(z)==64 and all(x['state']=='EMPTY' and x['wp']==x['slba'] for x in z)
PY
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
# 只格式化上面确认全空、由本次独立客机创建的FEMU命名空间。
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
common=(--fs_uri=zenfs://dev:nvme0n1 --db=/rrzc-gc-path --num=1048576 --value_size=4096
  --compression_type=none --disable_wal=1 --cache_size=0 --row_cache_size=0
  --use_direct_reads=1 --use_direct_io_for_flush_and_compaction=1 --verify_checksum=1
  --write_buffer_size=16777216 --max_write_buffer_number=3 --max_background_jobs=2
  --target_file_size_base=16777216 --max_bytes_for_level_base=134217728
  --level0_file_num_compaction_trigger=2 --level0_slowdown_writes_trigger=4
  --level0_stop_writes_trigger=8 --bloom_bits=10 --threads=1 --histogram=1
  --stats_interval=10000 --stats_interval_seconds=10 --seed=20261025
  --benchmark_write_rate_limit=0 --rate_limiter_bytes_per_sec=0)
printf '%q ' "${common[@]}" > "$result/公共参数.txt"
run_phase() {
  local phase=$1
  shift
  printf '%s\n' "$phase" > "$result/phase.txt"
  find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/$phase.logs.before.txt"
  # 清空继承环境，明确采用最少有效量Greedy。保留已验证的共同分配/内容安全实现。
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
    ZENFS_GC_PATH_TRACE=1 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 \
    ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_STRICT=0 ZENFS_RR_ENABLE=0 \
    ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
    ZENFS_RRZC_RESERVE_TARGET=1 ZENFS_RRZC_SINGLE_RECOVERY=1 \
    ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 \
    ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
    ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
    ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_ALIGN_RECENT=1 \
    ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_ETA_OBSERVE=1 \
    ZENFS_RRZC_DIRECT=1 ZENFS_RRZC_PAIR_GREEDY=1 ZENFS_RRZC_CANDIDATE_POOL=expanded \
    ZENFS_RRZC_WINDOW_CREDIT=0 ZENFS_RRZC_PERSISTENT_CREDIT=0 ZENFS_RRZC_EXPECTED_RR=0 \
    GC_PATH_PID_FILE="$result/bench.pid" \
    python3 -c 'import os,resource,sys;from pathlib import Path;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));Path(os.environ["GC_PATH_PID_FILE"]).write_text(str(os.getpid()));os.execv(sys.argv[1],sys.argv[1:])' \
    ./db_bench "${common[@]}" "$@" > "$result/$phase.log" 2>&1
  python3 - "$result" "$phase" <<'PY'
from pathlib import Path
import os,sys
r=Path(sys.argv[1]);phase=sys.argv[2]
old=set((r/(phase+'.logs.before.txt')).read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'维护日志无法唯一定位'
os.link(logs[0],r/(phase+'.zenfs.log'))
log=r/'aux/rrzc-gc-path/LOG'
assert log.is_file(),'缺少RocksDB日志'
os.link(log,r/(phase+'.rocksdb.log'))
PY
  printf '0\n' > "$result/$phase.exit"
  nvme zns report-zones /dev/nvme0n1 -o json > "$result/$phase.zones.json"
}
run_phase load --benchmarks=fillseq,flush,waitforcompaction --writes=1048576
# 固定上限，不因观察不到GC无限追加；出现完整GC后结束构造，随后校验。
for pass in 1 2 3 4; do
  run_phase "overwrite-$pass" --use_existing_db=1 --benchmarks=overwrite,flush,waitforcompaction --writes=262144
  if python3 - "$result" <<'PY'
from pathlib import Path
import sys
raise SystemExit(0 if any('GC路径复制：' in p.read_text() and 'ZC 统计：' in p.read_text()
    for p in Path(sys.argv[1]).glob('*.zenfs.log')) else 1)
PY
  then break; fi
done
run_phase verify --use_existing_db=1 --benchmarks=readseq,verifychecksum --reads=1048576 --writes=0
python3 "$target/gc_path_trace.py" "$result" > "$result/路径核验.json"
python3 - "$result/路径核验.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d['已闭环源Zone数']>0 and d['RR维护次数']==0,'没有观察到完整纯GC迁移，不宣称实验成功'
PY
printf 'done\n' > "$result/phase.txt"
