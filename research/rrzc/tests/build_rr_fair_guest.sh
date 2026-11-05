#!/usr/bin/env bash
# 仅独立子镜像修复RR地址饥饿；不改变ZC模型、阈值、周期和一轮一区行为。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h "$target/before/"
cp "$target/rrzc_rr_fair_queue.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-rr-fair-queue.patch"
git apply --unidiff-zero "$target/zenfs-rr-fair-queue.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-rr-fair-queue.patch"
cp fs/fs_zenfs.cc fs/fs_zenfs.h "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_rr_fair_queue.cc" -o "$target/model-unit"
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/rrzc_rr_fair_queue.h \
  plugin/zenfs/fs/rrzc_direct_zenfs.inc plugin/zenfs/fs/rrzc_direct_selector.h \
  plugin/zenfs/fs/rrzc_recent_eta_selector.h plugin/zenfs/fs/rrzc_stable_credit_selector.h \
  plugin/zenfs/fs/snapshot.h librocksdb_debug.a > "$target/software.sha256"
