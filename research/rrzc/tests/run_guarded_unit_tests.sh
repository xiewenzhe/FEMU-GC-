#!/usr/bin/env bash
# 只做编译和内存检查，不操作NVMe或客机。
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash research/rrzc/tests/run_cost_observe_unit_tests.sh
guarded_test_dir=$(mktemp -d /tmp/rrzc-guarded-tests.XXXXXX)
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_guarded.cc -o "$guarded_test_dir/core"
ASAN_OPTIONS=detect_leaks=0 "$guarded_test_dir/core"
