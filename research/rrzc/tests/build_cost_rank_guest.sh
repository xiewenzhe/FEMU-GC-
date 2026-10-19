#!/usr/bin/env bash
# 仅新构建子镜像：安装独立头与最小接线补丁，不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-cost-rank-20261016-build
trap 'code=$?; printf "%s\n" "$code" > "$directory/build.exit"; touch "$directory/finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $directory/before ]]
cd /home/femu/rocksdb
sha256sum -c "$directory/source.base.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
cd plugin/zenfs
git apply --check "$directory/zenfs-cost-rank.patch"
[[ ! -e fs/rrzc_cost_rank.h ]]
mkdir "$directory/before" "$directory/model" "$directory/tests"
cp fs/fs_zenfs.cc "$directory/before/"
cp "$directory/rrzc_cost_rank.h" fs/
git apply "$directory/zenfs-cost-rank.patch"
git apply --reverse --check "$directory/zenfs-cost-rank.patch"
cp fs/rrzc_*.h "$directory/model/"
cp "$directory/test_rrzc_cost_rank.cc" "$directory/tests/"
g++ -std=c++17 -Wall -Wextra -Werror "$directory/tests/test_rrzc_cost_rank.cc" -o "$directory/unit"
"$directory/unit" > "$directory/unit.log" 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$directory/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$directory/competition_workload_test.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 \
  -pthread -ldl -lrt -o "$directory/competition_workload_test" > "$directory/workload-build.log" 2>&1
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$directory/competition_workload_test" > "$directory/source.after.sha256"
cp plugin/zenfs/fs/fs_zenfs.cc "$directory/fs_zenfs.after.cc"
