#!/usr/bin/env bash
# 只在新构建子镜像增加可选更新范围；库、绑定和设备机制均保持原样。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/locality-20261019
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
mkdir "$target/before"
cp "$directory/ycsb/ycsb" "$target/before/ycsb"
cd "$directory/ycsb"
cp core/rrzc_independent_keys.h "$target/before/"
git apply --check "$target/ycsb-update-range.patch"
git apply "$target/ycsb-update-range.patch"
git apply --reverse --check "$target/ycsb-update-range.patch"
grep -v '^rrzc.update_key_' "$target/workload-locality-zns.properties" > "$target/workload-default.properties"
# 显式重编头文件消费者，避免沿用旧对象文件。
make -W core/core_workload.cc -W core/core_workload.h -W core/ycsbc.cc -j4 BIND_ROCKSDB=1 \
  EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
for arm in before default full first second; do
  mkdir "$target/selftest-$arm"
  binary=$directory/ycsb/ycsb
  [[ $arm != before ]] || binary=$target/before/ycsb
  flags=()
  [[ $arm != full ]] || flags=(-p rrzc.update_key_start=0 -p rrzc.update_key_count=1024)
  [[ $arm != first && $arm != second ]] || flags=(-p rrzc.update_key_start=64 -p rrzc.update_key_count=192)
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$binary" -load -run -db rocksdb -threads 1 -P "$target/workload-default.properties" \
    "${flags[@]}" -p rrzc.read_key_count=64 \
    -p recordcount=1024 -p operationcount=8192 -p rocksdb.fs_uri= \
    -p rocksdb.dbname="$target/selftest-$arm/db" -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 \
    -p rocksdb.write_buffer_size=1048576 -p rocksdb.target_file_size_base=1048576 \
    -p rocksdb.max_bytes_for_level_base=8388608 > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
  cp "$target/selftest-$arm/db/LOG" "$target/selftest-$arm/rocksdb.log"
done
for arm in default full; do
  cmp "$target/selftest-before/audit.requests" "$target/selftest-$arm/audit.requests"
  cmp "$target/selftest-before/audit.expected" "$target/selftest-$arm/audit.expected"
done
cmp "$target/selftest-first/audit.requests" "$target/selftest-second/audit.requests"
cmp "$target/selftest-first/audit.expected" "$target/selftest-second/audit.expected"
python3 "$target/check_ycsb_independent_trace.py" "$target/selftest-first/audit.requests" 1024 64 8192 \
  --update-range 64 192 > "$target/selftest-trace.json"
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
# 父清单只允许二进制和新增范围的头文件改变；库和其余源文件逐一核验。
python3 - "$target" "$directory" <<'PY'
from pathlib import Path
import sys
target=Path(sys.argv[1]);directory=sys.argv[2]
allowed={directory+'/ycsb/ycsb',directory+'/ycsb/core/rrzc_independent_keys.h'}
rows=(target/'parent.software.sha256').read_text().splitlines()
(target/'unchanged.sha256').write_text(''.join(s+'\n' for s in rows if s.split(maxsplit=1)[1] not in allowed))
PY
sha256sum -c "$target/unchanged.sha256" > "$target/software.unchanged.log"
cp "$target/unchanged.sha256" "$target/software.sha256"
sha256sum "$directory/ycsb/ycsb" "$directory/ycsb/core/rrzc_independent_keys.h" \
  "$target/run_ycsb_locality_guest.sh" "$target/workload-locality-zns.properties" \
  "$target/probe_ycsb_progress.py" "$target/check_ycsb_independent_trace.py" >> "$target/software.sha256"
