#!/usr/bin/env bash
# 仅新子镜像部署独立内核；不覆盖原模型及旧客户端，保留修改前源码。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc "$target/before/"
cp "$target/rrzc_stable_credit_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
for patch in zenfs-stable-credit.patch zenfs-stable-greedy-pair.patch; do
  git apply --unidiff-zero --check "$target/$patch"
  git apply --unidiff-zero "$target/$patch"
  git apply --unidiff-zero --reverse --check "$target/$patch"
done
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_stable_credit_selector.cc" -o "$target/model-unit"
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
# 仅声明本轮真实重链接的受控程序，不将旧YCSB二进制标记为新模型。
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc \
  plugin/zenfs/fs/snapshot.h plugin/zenfs/fs/rrzc_direct_selector.h \
  plugin/zenfs/fs/rrzc_recent_eta_selector.h plugin/zenfs/fs/rrzc_stable_credit_selector.h \
  librocksdb_debug.a "$target/joint_opportunity_smoke" "$target/run_stable_credit_guest.sh" \
  > "$target/software.sha256"
