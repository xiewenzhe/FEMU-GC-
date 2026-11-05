#!/usr/bin/env bash
# 新子镜像只重链接客户端和校验器；不改库、不覆盖原YCSB。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
old=/home/femu/rrzc-ycsb-retention-20261017-tests
[[ $# == 0 && $(id -un) == femu && ! -e $target/ycsb ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
sha256sum -c "$target/client.parent.sha256" > "$target/client.parent.log"
cp -a "$old/ycsb" "$target/ycsb"
cd "$target/ycsb"
# 显式更新主入口与绑定消费者，确保重新链接当前静态库而非复用旧二进制。
make --trace -W core/ycsbc.cc -W rocksdb/rocksdb_db.cc -j2 BIND_ROCKSDB=1 \
  EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
# 完整命令之外，再核对新二进制确实含近期ETA开关，不依赖旧静态客户端。
strings "$target/ycsb/ycsb" | grep -F 'ZENFS_RRZC_RECENT_ETA' > "$target/recent-eta-symbol.log"
cd /home/femu/rocksdb
for name in verify_ycsb sst_device_probe; do
  g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
    "$target/$name.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
    -o "$target/$name" > "$target/$name-build.log" 2>&1
done
for mode in disjoint overlap; do
  for repeat in first second; do
    out=$target/selftest-$mode-$repeat
    mkdir "$out"
    start=64
    [[ $mode != overlap ]] || start=0
    env -i PATH=/usr/bin:/bin LANG=C.UTF-8 YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$out/audit" \
      "$target/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$target/workload-sst-probe.properties" \
      -p recordcount=1024 -p operationcount=8192 -p rrzc.read_key_count=64 \
      -p rrzc.update_key_start="$start" -p rrzc.update_key_count=256 \
      -p rocksdb.fs_uri= -p rocksdb.dbname="$out/db" -p sleepafterload=0 -p limit.ops=0 \
      -p rocksdb.write_buffer_size=1048576 -p rocksdb.target_file_size_base=1048576 \
      -p rocksdb.max_bytes_for_level_base=8388608 -p status.interval=1 > "$out/ycsb.log" 2>&1
    "$target/verify_ycsb" posix "$out/db" "$out/audit.expected" > "$out/verify.log" 2>&1
    cp "$out/db/LOG" "$out/rocksdb.log"
    python3 "$target/sst_probe_trace.py" "$out/audit.requests" 1024 64 8192 "$start" 256 > "$out/trace.json"
  done
  cmp "$target/selftest-$mode-first/audit.requests" "$target/selftest-$mode-second/audit.requests"
  cmp "$target/selftest-$mode-first/audit.expected" "$target/selftest-$mode-second/audit.expected"
done
# 核心库、源和旧客户端必须逐字节保持不变。
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.after.log"
sha256sum -c "$target/client.parent.sha256" > "$target/client.parent.after.log"
cp "$target/parent.software.sha256" "$target/software.sha256"
sha256sum "$target/ycsb/ycsb" "$target/verify_ycsb" "$target/sst_device_probe" \
  "$target/ycsb/rocksdb/rocksdb_db.cc" "$target/ycsb/core/rrzc_independent_keys.h" \
  "$target/run_sst_probe_guest.sh" "$target/workload-sst-probe.properties" >> "$target/software.sha256"
printf '0\n' > "$target/selftest.exit"
