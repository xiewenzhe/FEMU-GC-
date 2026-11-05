#!/usr/bin/env bash
# 仅新隔离构建；两臂同库、只读时间观测相同，原策略默认保持不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc plugin/zenfs/fs/snapshot.h "$target/before/"
cp "$target/rrzc_recent_eta_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-recent-eta.patch"
git apply --unidiff-zero "$target/zenfs-recent-eta.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-recent-eta.patch"
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc fs/snapshot.h "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_recent_eta_selector.cc" -o "$target/model-unit"
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
# 不把未重链接的旧YCSB当作本轮新库；本轮只有受控功能程序。
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc \
  plugin/zenfs/fs/snapshot.h plugin/zenfs/fs/rrzc_direct_selector.h \
  plugin/zenfs/fs/rrzc_recent_eta_selector.h librocksdb_debug.a "$target/joint_opportunity_smoke" \
  "$target/run_recent_eta_guest.sh" > "$target/software.sha256"
