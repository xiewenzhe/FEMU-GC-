#!/usr/bin/env bash
# 仅在本轮新构建子镜像编译；不打开NVMe，不运行RR/ZC或性能实验。
set -euo pipefail
directory=/home/femu/rrzc-tail-ablation-build-20261015
[[ $# == 0 && $(id -un) == femu && ! -e $directory/build.started && ! -e $directory/before ]]
cd /home/femu/rocksdb/plugin/zenfs
sha256sum -c "$directory/source.guarded.sha256"
[[ ! -e fs/rrzc_tail_ablation.h ]]
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 3221225472 ]]
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_*.h > "$directory/source.before.sha256"
git apply --check "$directory/zenfs-tail-ablation.patch"
mkdir "$directory/before"
cp fs/fs_zenfs.cc "$directory/before/"
git apply "$directory/zenfs-tail-ablation.patch"
cp "$directory/model/rrzc_tail_ablation.h" fs/rrzc_tail_ablation.h
git apply --reverse --check "$directory/zenfs-tail-ablation.patch"
cp fs/rrzc_*.h "$directory/model/"
sha256sum fs/fs_zenfs.cc fs/fs_zenfs.h fs/zbd_zenfs.cc fs/zbd_zenfs.h \
  fs/zbdlib_zenfs.cc fs/zbdlib_zenfs.h fs/rrzc_*.h > "$directory/source.after.sha256"
date -u > "$directory/build.started"
cd /home/femu/rocksdb
status=0
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench \
  > "$directory/library-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/library-build.exit"
if [[ $status != 0 ]]; then tail -60 "$directory/library-build.log"; exit "$status"; fi
g++ -std=c++17 -Wall -Wextra -Werror "$directory/tests/test_rrzc_tail_ablation.cc" \
  -o "$directory/test_rrzc_tail_ablation" > "$directory/test-build.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/test-build.exit"
if [[ $status != 0 ]]; then cat "$directory/test-build.log"; exit "$status"; fi
"$directory/test_rrzc_tail_ablation" > "$directory/test-run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$directory/test-run.exit"
cat "$directory/test-run.log"
sha256sum librocksdb_debug.a db_bench "$directory/test_rrzc_tail_ablation" > "$directory/binaries.sha256"
exit "$status"
