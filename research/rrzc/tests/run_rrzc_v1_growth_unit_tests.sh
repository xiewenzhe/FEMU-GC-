#!/usr/bin/env bash
# 保留旧测试入口；新增准入模块仅运行内存测试，不访问块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash research/rrzc/tests/run_rrzc_v1_unit_tests.sh
growth_test_dir=$(mktemp -d /tmp/rrzc-v1-growth-tests.XXXXXX)
printf '增长准入构建目录：%s\n' "$growth_test_dir"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_v1_growth.cc -o "$growth_test_dir/growth"
ASAN_OPTIONS=detect_leaks=0 "$growth_test_dir/growth"
