#!/usr/bin/env bash
# 仅新隔离构建：部署已经完成的零门槛支持，不修改模型公式或RR/GC。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_weighted_selector.h "$target/before/"
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-zero-reclaim.patch"
git apply --unidiff-zero "$target/zenfs-zero-reclaim.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-zero-reclaim.patch"
cp "$target/rrzc_weighted_selector.h" fs/
cp fs/fs_zenfs.cc "$target/fs_zenfs.compiled.cc"
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_weighted_selector.h \
  plugin/zenfs/fs/rrzc_direct_selector.h plugin/zenfs/fs/rrzc_direct_zenfs.inc \
  librocksdb_debug.a "$target/joint_opportunity_smoke" > "$target/software.sha256"
