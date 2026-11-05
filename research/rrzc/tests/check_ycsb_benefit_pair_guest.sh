#!/usr/bin/env bash
# 同已编译库的普通目录双次回归；不打开NVMe，不改变正式负载。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/benefit-pair-20261019
trap 'code=$?; printf "%s\n" "$code" > "$target/selftest.exit"; touch "$target/selftest.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/selftest-first ]]
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$target/software.before.log"
for arm in first second; do
  mkdir "$target/selftest-$arm"
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$target/workload-locality-wide-zns.properties" \
    -p recordcount=2560 -p operationcount=8192 -p rrzc.read_key_count=16 -p fieldlength=4096 \
    -p rrzc.update_key_start=16 -p rrzc.update_key_count=1008 \
    -p rocksdb.fs_uri= -p rocksdb.dbname="$target/selftest-$arm/db" \
    -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
  python3 "$target/check_ycsb_independent_trace.py" "$target/selftest-$arm/audit.requests" 2560 16 8192 --update-range 16 1008 \
    > "$target/selftest-$arm/trace.json"
done
cmp "$target/selftest-first/audit.requests" "$target/selftest-second/audit.requests"
cmp "$target/selftest-first/audit.expected" "$target/selftest-second/audit.expected"
sha256sum -c "$target/software.sha256" > "$target/software.after.log"
