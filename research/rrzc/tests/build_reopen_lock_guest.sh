#!/usr/bin/env bash
# 仅新构建子镜像：补丁、完整库构建和内存并发回归，不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-reopen-lock-build-20261015
[[ $# == 0 && $(id -un) == femu && ! -e $directory/before && ! -e $directory/build.started ]]
cd /home/femu/rocksdb/plugin/zenfs
sha256sum -c "$directory/source.base.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/io_zenfs.cc fs/io_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_*.h > "$directory/source.before.sha256"
git apply --check "$directory/zenfs-reopen-lock.patch"
mkdir "$directory/before"
cp fs/fs_zenfs.cc "$directory/before/"
git apply "$directory/zenfs-reopen-lock.patch"
git apply --reverse --check "$directory/zenfs-reopen-lock.patch"
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/io_zenfs.cc fs/io_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_*.h > "$directory/source.after.sha256"
cp fs/fs_zenfs.cc "$directory/fs_zenfs.fixed.cc"
date -u > "$directory/build.started"
cd /home/femu/rocksdb
status=0
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench \
  > "$directory/library-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/library-build.exit"
if [[ $status != 0 ]]; then tail -40 "$directory/library-build.log"; exit "$status"; fi
for variant in old fixed; do
  mkdir "$directory/$variant"
  source_file=$directory/before/fs_zenfs.cc
  if [[ $variant == fixed ]]; then source_file=$directory/fs_zenfs.fixed.cc; fi
  awk '/^IOStatus ZenFS::OpenWritableFile\(/ { on=1 } on { print } on && /^}$/ { on=0 }' \
    "$source_file" > "$directory/$variant/reopen-under-test.inc"
  g++ -std=c++20 -Wall -Wextra -Werror -pthread -I "$directory/$variant" \
    "$directory/test_reopen_lock.cc" -o "$directory/$variant/test" \
    > "$directory/$variant/build.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$directory/$variant/build.exit"
  [[ $status == 0 ]]
done
timeout 15s "$directory/old/test" --expect-old > "$directory/old/run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/old/run.exit"
[[ $status == 0 ]]
timeout 15s "$directory/fixed/test" > "$directory/fixed/run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/fixed/run.exit"
cat "$directory/old/run.log" "$directory/fixed/run.log"
sha256sum librocksdb_debug.a db_bench > "$directory/binaries.sha256"
exit "$status"
