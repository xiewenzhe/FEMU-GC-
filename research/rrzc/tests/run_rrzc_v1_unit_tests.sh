#!/usr/bin/env bash
# 仅生成隔离构建产物并运行内存测试；不启动虚拟机，不访问或重置块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
test_dir=$(mktemp -d /tmp/rrzc-v1-tests.XXXXXX)
printf '测试构建目录：%s\n' "$test_dir"
# 当前受追踪环境不支持泄漏扫描；保留地址及未定义行为检查。
export ASAN_OPTIONS="detect_leaks=0"
awk '/^enum ZnsRrRecentFlags / { on=1 } on { print }
     /^} ZnsRrRecentEntry;/ { on=0 }' hw/femu/nvme.h > "$test_dir/rr-recent-wire-under-test.inc"
awk '/^static uint16_t zns_rr_recent_log\(/ { on=1 }
     on { print } on && /^}$/ { on=0 }' hw/femu/zns/zns.c > "$test_dir/rr-recent-device-under-test.inc"
awk '/^static void zns_reset_block_state\(/ || /^static uint64_t zns_read\(/ { on=1 }
     on { print } on && /^}$/ { on=0 }' hw/femu/zns/zftl.c >> "$test_dir/rr-recent-device-under-test.inc"
# 查询函数全部是新增行；直接从被版本管理的补丁提取，避免依赖旧客机源镜像。
awk '/^\+IOStatus ZbdlibBackend::GetZoneRecentReads\(/ { on=1 }
     on { print substr($0,2) } on && /^\+}$/ { on=0 }' \
  research/rrzc/zenfs-v1-shadow.patch > "$test_dir/rr-recent-backend-under-test.inc"
test -s "$test_dir/rr-recent-backend-under-test.inc"
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rr_history.c -o "$test_dir/history"
cc -std=gnu11 -Wall -Wextra -Werror -Wno-sign-compare -pthread \
  -fsanitize=address,undefined -I "$test_dir" \
  research/rrzc/tests/test_rr_recent_device.c -o "$test_dir/device"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_v1.cc -o "$test_dir/selector"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -I "$test_dir" \
  research/rrzc/tests/test_rr_recent_backend.cc -o "$test_dir/backend"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_v1_execution.cc -o "$test_dir/execution"
"$test_dir/history"
"$test_dir/device"
"$test_dir/selector"
"$test_dir/backend"
"$test_dir/execution"
