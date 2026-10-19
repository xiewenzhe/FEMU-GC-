#!/usr/bin/env bash
# 沿用旧内存测试入口，新影子模块独立编译，不访问或重置块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash research/rrzc/tests/run_rrzc_v1_growth_unit_tests.sh
short_test_dir=$(mktemp -d /tmp/rrzc-short-shadow-tests.XXXXXX)
printf '短期影子构建目录：%s\n' "$short_test_dir"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_short_shadow.cc -o "$short_test_dir/short-shadow"
ASAN_OPTIONS=detect_leaks=0 "$short_test_dir/short-shadow"
