#!/usr/bin/env bash
# 提取真实生产诊断函数；仅构建内存测试，不读取或格式化块设备。
set -euo pipefail
ulimit -c 0
cd "$(dirname "$0")/../../.."
test_dir=build-femu/rrzc/maintenance-diagnostics-test
mkdir -p "$test_dir"
awk '
  /^IOStatus Zone::Reset\(/ || /^ZoneResetState Zone::GetResetState\(/ ||
  /^std::map<uint64_t, ZoneResetState> ZonedBlockDevice::GetZoneResetStates\(/ ||
  /^void ZonedBlockDevice::LogZoneReset\(/ { selected=1 }
  selected { print }
  selected && /^}$/ { selected=0 }
' build-femu/rrzc/zenfs-source/fs/zbd_zenfs.cc > "$test_dir/diagnostics-under-test.inc"
awk '
  /^static bool IsMaintenanceWindowStable\(/ || /^static void LogMaintenanceObservation\(/ { selected=1 }
  selected { print }
  selected && /^}$/ { selected=0 }
' build-femu/rrzc/zenfs-source/fs/fs_zenfs.cc >> "$test_dir/diagnostics-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -I "$test_dir" \
  research/rrzc/tests/maintenance_diagnostics_test.cc -o "$test_dir/maintenance_diagnostics_test"
"$test_dir/maintenance_diagnostics_test"
