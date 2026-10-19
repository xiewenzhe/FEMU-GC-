#!/usr/bin/env bash
# 只提取当前真实函数做受控内存复现，不启动客机或访问块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
retirement_probe_dir=$(mktemp -d /tmp/rrzc-retirement-probe.XXXXXX)
printf '并发退役复现目录：%s\n' "$retirement_probe_dir"
awk '/^IOStatus ZenFS::MigrateExtents\(/ || /^IOStatus ZenFS::MigrateFileExtents\(/ {on=1} on {print} on && /^}$/ {on=0}' \
  build-femu/rrzc/migration-window-work/fs/fs_zenfs.cc > "$retirement_probe_dir/migration-under-test.inc"
test -s "$retirement_probe_dir/migration-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -fsanitize=address,undefined \
  -Iresearch/rrzc/model -I"$retirement_probe_dir" research/rrzc/tests/migration_retirement_probe.cc -o "$retirement_probe_dir/probe"
ASAN_OPTIONS=detect_leaks=0 "$retirement_probe_dir/probe"
