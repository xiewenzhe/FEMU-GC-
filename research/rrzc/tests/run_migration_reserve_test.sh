#!/usr/bin/env bash
# 从生产文件提取实际函数，构建产物仅写入工作区，不接触块设备。
set -euo pipefail
ulimit -c 0
cd "$(dirname "$0")/../../.."
test_dir=build-femu/rrzc/migration-reserve-test
mkdir -p "$test_dir"
awk '
  /^IOStatus ZonedBlockDevice::(ResetUnusedIOZones|AllocateEmptyZone|EnsureMigrationReserve|PrepareReadReclaimSource|ReleaseMigrateZone|TakeMigrateZone)\(/ { selected=1 }
  selected { print }
  selected && /^}$/ { selected=0 }
' build-femu/rrzc/zenfs-source/fs/zbd_zenfs.cc > "$test_dir/migration-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I "$test_dir" \
  research/rrzc/tests/migration_reserve_test.cc -o "$test_dir/migration_reserve_test"
"$test_dir/migration_reserve_test"
