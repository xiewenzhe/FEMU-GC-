#!/usr/bin/env bash
# 仅生成工作区构建产物，不运行设备命令，不覆盖实验日志。
set -euo pipefail
ulimit -c 0
cd "$(dirname "$0")/../../.."
test_dir=build-femu/rrzc/allocation-fallback-test
mkdir -p "$test_dir"
awk '
  /^IOStatus ZonedBlockDevice::AllocateIOZone\(/ { in_function=1 }
  /^std::string ZonedBlockDevice::GetFilename\(/ { in_function=0 }
  in_function { print }
' build-femu/rrzc/zenfs-source/fs/zbd_zenfs.cc > "$test_dir/allocator-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -I "$test_dir" \
  research/rrzc/tests/allocation_fallback_test.cc -o "$test_dir/allocation_fallback_test"
"$test_dir/allocation_fallback_test"
