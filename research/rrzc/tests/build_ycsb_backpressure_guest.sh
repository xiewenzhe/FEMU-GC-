#!/usr/bin/env bash
# 仅核验新反压配置，不重编或改写原有YCSB、ZenFS和RocksDB二进制。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/backpressure-20261018
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/selftest-first ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
cp "$target/workload-pressure-backpressure-zns.properties" "$target/workload-pressure-warmup-zns.properties"
cp "$target/workload-read-backpressure-zns.properties" "$target/workload-read-latest-zns.properties"
for arm in first second; do
  mkdir "$target/selftest-$arm"
  for stage in warmup evaluate; do
    properties=$target/workload-pressure-warmup-zns.properties
    flags=(-load -run)
    inheritance=()
    if [[ $stage == evaluate ]]; then
      properties=$target/workload-read-latest-zns.properties
      flags=(-run)
      inheritance=(YCSB_AUDIT_IMPORT="$target/selftest-$arm/warmup.expected" YCSB_AUDIT_KEYS=1024)
    fi
    # 普通目录小测试缩小缓冲区，实际产生SST和压实；真实设备参数不变。
    env YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/$stage" "${inheritance[@]}" \
      "$directory/ycsb/ycsb" "${flags[@]}" -db rocksdb -threads 1 -P "$properties" \
      -p recordcount=1024 -p operationcount=8192 -p fieldlength=4096 -p rocksdb.fs_uri= \
      -p rocksdb.dbname="$target/selftest-$arm/db" -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 \
      -p rocksdb.write_buffer_size=1048576 -p rocksdb.target_file_size_base=1048576 \
      -p rocksdb.max_bytes_for_level_base=8388608 > "$target/selftest-$arm/$stage.log" 2>&1
  done
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/evaluate.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
  python3 - "$target/selftest-$arm/db" <<'PY'
from pathlib import Path
import re,sys
texts=[p.read_text() for p in Path(sys.argv[1]).glob('LOG*') if p.is_file()]
for key,value in (('level0_file_num_compaction_trigger','2'),('level0_slowdown_writes_trigger','4'),('level0_stop_writes_trigger','8')):
    actual=set(re.findall(r'Options\.'+key+r':\s*(\d+)', '\n'.join(texts)))
    assert actual=={value}, (key,actual)
assert any('compaction_started' in text for text in texts), '小测试没有真正压实'
PY
done
for stage in warmup evaluate; do
  cmp "$target/selftest-first/$stage.requests" "$target/selftest-second/$stage.requests"
  cmp "$target/selftest-first/$stage.expected" "$target/selftest-second/$stage.expected"
done
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.unchanged.log"
cp "$target/parent.software.sha256" "$target/software.sha256"
sha256sum "$target/run_ycsb_overlap_guest.sh" "$target/probe_ycsb_overlap.py" "$target/probe_ycsb_progress.py" \
  "$target/build_ycsb_backpressure_guest.sh" "$target/workload-pressure-warmup-zns.properties" \
  "$target/workload-read-latest-zns.properties" >> "$target/software.sha256"
