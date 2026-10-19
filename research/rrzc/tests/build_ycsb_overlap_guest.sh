#!/usr/bin/env bash
# 只重编译请求审计入口；不修改库、FEMU或公开请求生成器，不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/overlap-20261018
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 1073741824 ]]
mkdir "$target/before"
cp "$directory/ycsb/rrzc_ycsb_audit.h" "$target/before/"
cp "$target/rrzc_ycsb_audit.h" "$directory/ycsb/"
g++ -std=c++17 -Wall -Wextra -Werror -I"$directory/ycsb" \
  "$target/test_ycsb_audit_import.cc" -o "$target/audit-unit"
cd "$directory/ycsb"
# 指定源文件为更新，必须出现对象编译；只指定.o仅能保证重新链接。
make -W core/db_factory.cc -j4 BIND_ROCKSDB=1 EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
grep -Eq 'CC[[:space:]]+core/db_factory.o' "$target/ycsb-build.log"
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
    env YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/$stage" "${inheritance[@]}" \
      "$directory/ycsb/ycsb" "${flags[@]}" -db rocksdb -threads 1 -P "$properties" \
      -p recordcount=1024 -p operationcount=4096 -p fieldlength=128 -p rocksdb.fs_uri= \
      -p rocksdb.dbname="$target/selftest-$arm/db" -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 \
      > "$target/selftest-$arm/$stage.log" 2>&1
  done
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/evaluate.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
  grep -Fxq '装载=0' "$target/selftest-$arm/evaluate.counts"
  grep -Fxq '继承键数=1024' "$target/selftest-$arm/evaluate.counts"
done
for stage in warmup evaluate; do
  cmp "$target/selftest-first/$stage.requests" "$target/selftest-second/$stage.requests"
  cmp "$target/selftest-first/$stage.expected" "$target/selftest-second/$stage.expected"
done
# 默认关闭时仍允许原来的装载＋业务单进程路径。
mkdir "$target/selftest-default"
YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-default/audit" \
  "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$target/workload-read-latest-zns.properties" \
  -p recordcount=1024 -p operationcount=4096 -p fieldlength=128 -p rocksdb.fs_uri= \
  -p rocksdb.dbname="$target/selftest-default/db" -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 \
  > "$target/selftest-default/ycsb.log" 2>&1
"$directory/verify_ycsb" posix "$target/selftest-default/db" "$target/selftest-default/audit.expected" \
  > "$target/selftest-default/verify.log" 2>&1
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
while read -r digest name; do sha256sum "$name"; done < "$target/parent.software.sha256" > "$target/software.sha256"
sha256sum "$directory/ycsb/rrzc_ycsb_audit.h" "$target/run_ycsb_overlap_guest.sh" \
  "$target/workload-pressure-warmup-zns.properties" "$target/workload-read-latest-zns.properties" \
  "$target/probe_ycsb_overlap.py" "$target/test_ycsb_audit_import.cc" >> "$target/software.sha256"
