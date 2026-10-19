#!/usr/bin/env bash
# 提取已编译成本观察源中的真实资源函数；不访问NVMe或修改旧测试。
set -euo pipefail
cd "$(dirname "$0")/../../.."
tail_test_dir=$(mktemp -d /tmp/rrzc-tail-branches.XXXXXX)
awk '/^int main\(\)/ { exit } { print }' research/rrzc/tests/migration_reserve_test.cc \
  > "$tail_test_dir/migration-fixture.inc"
awk '/^IOStatus ZonedBlockDevice::(ResetUnusedIOZones|AllocateEmptyZone|EnsureMigrationReserve|PrepareReadReclaimSource|ReleaseMigrateZone|TakeMigrateZone)\(/ { selected=1 }
     selected { print } selected && /^}$/ { selected=0 }' \
  build-femu/rrzc/cost-observe-work/fs/zbd_zenfs.cc > "$tail_test_dir/migration-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -fsanitize=address,undefined -I "$tail_test_dir" \
  research/rrzc/tests/test_tail_branches.cc -o "$tail_test_dir/branches"
ASAN_OPTIONS=detect_leaks=0 "$tail_test_dir/branches"
