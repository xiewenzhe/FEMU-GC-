#!/usr/bin/env bash
# 仅编译后续压力客户端；已验证的ZenFS库、所有模型和RR代码逐字节不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
cd "$target"
mkdir before
cp joint_opportunity_smoke.cc before/
git apply --check joint-followup-client.patch
git apply joint-followup-client.patch
git apply --reverse --check joint-followup-client.patch
cd /home/femu/rocksdb
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -DRRZC_JOINT_TRACE_AVAILABLE -Iplugin/zenfs/fs -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_time_smoke" > "$target/program-build.log" 2>&1
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.after.log"
cp "$target/parent.software.sha256" "$target/software.sha256"
find "$target" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.cc' -o -name '*.h' -o -name '*.py' -o -name '*.patch' -o -name 'joint_time_smoke' \) \
  -exec sha256sum {} + >> "$target/software.sha256"
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
