#!/usr/bin/env bash
# 只修改新子镜像；先核对原库身份，不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-rocksdb-pair-rate-20261016-tests
trap 'code=$?; printf "%s\n" "$code" > "$directory/build.exit"; touch "$directory/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $directory/before ]]
cd /home/femu/rocksdb
sha256sum -c "$directory/source.base.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
cd plugin/zenfs
git apply --check "$directory/zenfs-pair-rate.patch"
[[ ! -e fs/rrzc_pair_rate.h ]]
mkdir "$directory/before"
cp fs/fs_zenfs.cc "$directory/before/"
cp "$directory/rrzc_pair_rate.h" fs/
git apply "$directory/zenfs-pair-rate.patch"
git apply --reverse --check "$directory/zenfs-pair-rate.patch"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -Ifs "$directory/test_pair_rate.cc" -o "$directory/sampler-unit"
"$directory/sampler-unit" > "$directory/unit.log" 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$directory/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$directory/rocksdb_observe_test.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 \
  -pthread -ldl -lrt -o "$directory/rocksdb_observe_test" > "$directory/workload-build.log" 2>&1
g++ -std=c++17 -Wall -Wextra -Werror "$directory/test_rocksdb_observe_plan.cc" -o "$directory/plan"
"$directory/plan" > "$directory/plan.log"
mkdir "$directory/selftest"
status=0
RRZC_TEST_DB_PROFILE=pair90 "$directory/rocksdb_observe_test" selftest "$directory/selftest" > "$directory/selftest.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/selftest.exit"
[[ $status == 0 ]]
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a > "$directory/source.after.sha256"
