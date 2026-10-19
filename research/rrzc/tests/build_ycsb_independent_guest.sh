#!/usr/bin/env bash
# 只在新子镜像修改YCSB请求选键；不改静态库、绑定、验证器或设备数据。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/independent-20261018
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
mkdir "$target/before"
cp "$directory/ycsb/ycsb" "$target/before/ycsb"
cd "$directory/ycsb"
cp core/core_workload.h core/core_workload.cc "$target/before/"
git apply --check "$target/ycsb-independent-keys.patch"
git apply "$target/ycsb-independent-keys.patch"
git apply --reverse --check "$target/ycsb-independent-keys.patch"
grep -v '^rrzc\.' "$target/workload-independent-zns.properties" > "$target/workload-default.properties"
# 类大小变化要求重新编译工作负载与主程序，不能只强制重新链接。
make -W core/core_workload.cc -W core/core_workload.h -W core/ycsbc.cc -j4 BIND_ROCKSDB=1 \
  EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
for arm in before default disabled first second; do
  mkdir "$target/selftest-$arm"
  binary=$directory/ycsb/ycsb
  [[ $arm != before ]] || binary=$target/before/ycsb
  enabled=false
  [[ $arm != first && $arm != second ]] || enabled=true
  # 原二进制不认识新选键开关，只有原始分布；新版关闭时必须逐字节一致。
  flags=()
  [[ $arm != disabled && $enabled != true ]] || flags=(-p rrzc.independent_keys="$enabled")
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
for arm in default disabled; do
  cmp "$target/selftest-before/audit.requests" "$target/selftest-$arm/audit.requests"
  cmp "$target/selftest-before/audit.expected" "$target/selftest-$arm/audit.expected"
done
cmp "$target/selftest-first/audit.requests" "$target/selftest-second/audit.requests"
cmp "$target/selftest-first/audit.expected" "$target/selftest-second/audit.expected"
python3 "$target/check_ycsb_independent_trace.py" "$target/selftest-first/audit.requests" 1024 64 8192 \
  > "$target/selftest-trace.json"
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
# 父清单中只有YCSB二进制允许改变；剩余条目逐一核验。
grep -vF "$directory/ycsb/ycsb" "$target/parent.software.sha256" > "$target/unchanged.sha256"
sha256sum -c "$target/unchanged.sha256" > "$target/software.unchanged.log"
cp "$target/unchanged.sha256" "$target/software.sha256"
sha256sum "$directory/ycsb/ycsb" "$directory/ycsb/core/core_workload.cc" \
  "$directory/ycsb/core/core_workload.h" "$directory/ycsb/core/rrzc_independent_keys.h" \
  "$target/run_ycsb_independent_guest.sh" "$target/workload-independent-zns.properties" >> "$target/software.sha256"
