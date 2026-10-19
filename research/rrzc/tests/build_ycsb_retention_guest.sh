#!/usr/bin/env bash
# 只在全新构建子镜像修改ZenFS实验入口并编译；不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
trap 'code=$?; printf "%s\n" "$code" > "$directory/build.exit"; touch "$directory/build.finished"' EXIT
[[ $(id -un) == femu ]]
if [[ $# == 1 && $1 == resume ]]; then
  [[ -d $directory/before && -d $directory/首次编译失败 && ! -e $directory/ycsb ]]
else
[[ $# == 0 && ! -e $directory/before ]]
cd /home/femu/rocksdb
sha256sum -c "$directory/source.base.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
mkdir "$directory/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h "$directory/before/"
cd plugin/zenfs
[[ ! -e fs/rrzc_retention_selector.h && ! -e fs/rrzc_migration_window.h ]]
git apply --check "$directory/zenfs-migration-window.patch"
cp "$directory/rrzc_migration_window.h" fs/
git apply "$directory/zenfs-migration-window.patch"
git apply --check "$directory/zenfs-retention-selector.patch"
cp "$directory/rrzc_retention_selector.h" fs/
git apply "$directory/zenfs-retention-selector.patch"
git apply --reverse --check "$directory/zenfs-retention-selector.patch"
fi
cd "$directory"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I/home/femu/rocksdb/plugin/zenfs/fs test_retention_selector.cc -o retention-unit
./retention-unit > retention-unit.log 2>&1
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I/home/femu/rocksdb/plugin/zenfs/fs test_migration_window.cc -o window-unit
./window-unit > window-unit.log 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$directory/library-build.log" 2>&1
cd "$directory"
mkdir ycsb
tar -xzf ycsb-source.tar.gz -C ycsb
cp rrzc_ycsb_audit.h ycsb/
cd ycsb
cmake -S HdrHistogram_c -B HdrHistogram_c -DCMAKE_BUILD_TYPE=Release -DHDR_HISTOGRAM_BUILD_PROGRAMS=OFF > "$directory/hdr-configure.log" 2>&1
make -j4 BIND_ROCKSDB=1 EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$directory/ycsb-build.log" 2>&1
cd /home/femu/rocksdb
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$directory/verify_ycsb.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 \
  -pthread -ldl -lrt -o "$directory/verify_ycsb" > "$directory/verify-build.log" 2>&1
# 两次普通目录回归检查输入轨迹一致、上游读写、精确均值及全键最新值。
for arm in first second; do
  mkdir "$directory/selftest-$arm"
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$directory/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$directory/ycsb/workloads/workloada" \
    -p recordcount=1024 -p operationcount=4096 -p fieldcount=1 -p fieldlength=128 -p writeallfields=true \
    -p rocksdb.dbname="$directory/selftest-$arm/db" -p rocksdb.no_block_cache=true -p status=true -p status.interval=1 \
    > "$directory/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$directory/selftest-$arm/db" "$directory/selftest-$arm/audit.expected" \
    > "$directory/selftest-$arm/verify.log" 2>&1
done
cmp "$directory/selftest-first/audit.requests" "$directory/selftest-second/audit.requests"
cmp "$directory/selftest-first/audit.expected" "$directory/selftest-second/audit.expected"
printf '0\n' > "$directory/selftest.exit"
cd /home/femu/rocksdb
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$directory/ycsb/ycsb" "$directory/verify_ycsb" \
  "$directory/run_ycsb_retention_guest.sh" "$directory/workloada-zns.properties" > "$directory/software.sha256"
