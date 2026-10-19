#!/usr/bin/env bash
# 只在新子镜像核验、接线和编译，不打开NVMe，不修改旧镜像。
set -euo pipefail
directory=/home/femu/rrzc-rocksdb-migration-observe-20261017-tests
trap 'code=$?; printf "%s\n" "$code" > "$directory/build.exit"; touch "$directory/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $directory/before ]]
cd /home/femu/rocksdb
sha256sum -c "$directory/source.base.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
cd plugin/zenfs
[[ ! -e fs/rrzc_migration_window.h ]]
git apply --check "$directory/zenfs-migration-window.patch"
mkdir "$directory/before"
cp fs/fs_zenfs.cc fs/fs_zenfs.h "$directory/before/"
cp "$directory/rrzc_migration_window.h" fs/
git apply "$directory/zenfs-migration-window.patch"
git apply --reverse --check "$directory/zenfs-migration-window.patch"
cd "$directory"
cp rocksdb_observe_test.cc before/rocksdb_observe_test.cc
git apply --check rocksdb_observe_migration.patch
git apply rocksdb_observe_migration.patch
git apply --reverse --check rocksdb_observe_migration.patch
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I/home/femu/rocksdb/plugin/zenfs/fs test_migration_window.cc -o window-unit
./window-unit > unit.log 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$directory/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$directory/rocksdb_observe_test.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 \
  -pthread -ldl -lrt -o "$directory/rocksdb_observe_test" > "$directory/workload-build.log" 2>&1
for test in test_rocksdb_observe_plan test_rocksdb_anchor_plan; do
  g++ -std=c++17 -Wall -Wextra -Werror "$directory/$test.cc" -o "$directory/$test"
  "$directory/$test" > "$directory/$test.log"
done
mkdir "$directory/selftest"
status=0
RRZC_TEST_DB_PROFILE=anchors180slow "$directory/rocksdb_observe_test" selftest "$directory/selftest" > "$directory/selftest.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/selftest.exit"
[[ $status == 0 ]]
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a > "$directory/source.after.sha256"
