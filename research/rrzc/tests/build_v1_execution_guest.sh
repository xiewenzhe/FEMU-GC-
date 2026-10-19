#!/usr/bin/env bash
# 仅在本轮隔离子镜像编译；不访问NVMe、不运行GC/RR、不改父镜像。
set -euo pipefail
directory=/home/femu/rrzc-v1-execution-build-20261014
[[ $(id -un) == femu && $# == 0 ]]
[[ -d $directory && ! -e $directory/before && ! -e $directory/build.started ]]
cd /home/femu/rocksdb
sha256sum -c "$directory/source.shadow.sha256"
cd plugin/zenfs
git apply --check "$directory/zenfs-v1-execution.patch"
mkdir "$directory/before"
cp fs/fs_zenfs.cc "$directory/before/"
git apply "$directory/zenfs-v1-execution.patch"
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_recent_log.h \
  fs/rrzc_urgency_selector.h fs/rrzc_v1_execution.h > "$directory/source.after.sha256"
date -u > "$directory/build.started"
cd /home/femu/rocksdb
status=0
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench \
  > "$directory/library-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/library-build.exit"
if [[ $status != 0 ]]; then tail -60 "$directory/library-build.log"; exit "$status"; fi
g++ -std=c++17 -Wall -Wextra -Werror "$directory/tests/test_rrzc_v1_execution.cc" \
  -o "$directory/test_rrzc_v1_execution" > "$directory/test-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/test-build.exit"
if [[ $status != 0 ]]; then cat "$directory/test-build.log"; exit "$status"; fi
"$directory/test_rrzc_v1_execution" > "$directory/test-run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/test-run.exit"
cat "$directory/test-run.log"
sha256sum librocksdb_debug.a db_bench "$directory/test_rrzc_v1_execution" \
  > "$directory/binaries.sha256"
exit "$status"
