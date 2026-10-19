#!/usr/bin/env bash
# 仅在已确认的新联调子镜像执行；备份源文件，不修改父镜像或既有结果。
set -euo pipefail
directory=/home/femu/rrzc-v1-link-20261014
[[ $(id -un) == femu && $# == 0 ]]
[[ -d $directory && ! -e $directory/before && ! -e $directory/build.started ]]
cd /home/femu/rocksdb/plugin/zenfs
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h > "$directory/source.before.sha256"
sha256sum -c "$directory/base.sha256"
git apply --check "$directory/zenfs-v1-shadow.patch"
mkdir "$directory/before"
cp fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h "$directory/before/"
git apply "$directory/zenfs-v1-shadow.patch"
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_recent_log.h \
  fs/rrzc_urgency_selector.h > "$directory/source.after.sha256"
date -u > "$directory/build.started"
cd /home/femu/rocksdb
status=0
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench \
  > "$directory/library-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/library-build.exit"
if [[ $status != 0 ]]; then tail -60 "$directory/library-build.log"; exit "$status"; fi
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -I. -Iinclude "$directory/v1_recent_guest_test.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$directory/v1_recent_guest_test" > "$directory/test-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/test-build.exit"
cat "$directory/test-build.log"
sha256sum librocksdb_debug.a db_bench "$directory/v1_recent_guest_test" > "$directory/binaries.sha256"
exit "$status"
