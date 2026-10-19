#!/usr/bin/env bash
# 新子镜像只追加真实有效字节判空修复；不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/exact-live-20261017
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h "$target/before/"
cd plugin/zenfs
[[ -e fs/rrzc_empty_reserve.h && ! -e fs/rrzc_exact_live.h ]]
git apply --check "$target/zenfs-exact-live.patch"
cp "$target/rrzc_exact_live.h" fs/
git apply "$target/zenfs-exact-live.patch"
git apply --reverse --check "$target/zenfs-exact-live.patch"
cd "$target"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I/home/femu/rocksdb/plugin/zenfs/fs test_single_recovery.cc -o target-unit
./target-unit > target-unit.log 2>&1
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I/home/femu/rocksdb/plugin/zenfs/fs test_exact_live.cc -o exchange-unit
./exchange-unit > exchange-unit.log 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$target/library-build.log" 2>&1
cd "$directory/ycsb"
# 静态库变化后显式重新链接，不依赖上游Makefile是否跟踪外部库。
make -W core/db_factory.o -j4 BIND_ROCKSDB=1 EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
cd /home/femu/rocksdb
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$directory/verify_ycsb.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 \
  -pthread -ldl -lrt -o "$directory/verify_ycsb" > "$target/verify-build.log" 2>&1
for arm in default nowal-first nowal-second; do
  mkdir "$target/selftest-$arm"
  extra=()
  [[ $arm == default ]] || extra=(-p rocksdb.disable_wal=true)
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$directory/ycsb/workloads/workloada" \
    -p recordcount=1024 -p operationcount=4096 -p fieldcount=1 -p fieldlength=128 -p writeallfields=true \
    -p rocksdb.dbname="$target/selftest-$arm/db" -p rocksdb.no_block_cache=true -p status=true -p status.interval=1 \
    "${extra[@]}" > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
done
grep -Fq '实验WAL配置：关闭=0' "$target/selftest-default/ycsb.log"
for arm in nowal-first nowal-second; do
  grep -Fq '实验WAL配置：关闭=1' "$target/selftest-$arm/ycsb.log"
  cmp "$target/selftest-default/audit.requests" "$target/selftest-$arm/audit.requests"
  cmp "$target/selftest-default/audit.expected" "$target/selftest-$arm/audit.expected"
done
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$directory/ycsb/ycsb" "$directory/verify_ycsb" \
  "$directory/ycsb/rocksdb/rocksdb_db.cc" "$target/run_ycsb_exact_live_guest.sh" \
  "$target/workloada-zns-nowal.properties" > "$target/software.sha256"
