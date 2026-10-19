#!/usr/bin/env bash
# 只运行内存与合成分析测试，不访问块设备，不启动客机。
set -euo pipefail
cd "$(dirname "$0")/../../.."
pair_test_dir=$(mktemp -d /tmp/rrzc-pair-rate-tests.XXXXXX)
printf '双点估速测试目录：%s\n' "$pair_test_dir"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -Iresearch/rrzc/model \
  -fsanitize=address,undefined research/rrzc/tests/test_pair_rate.cc -o "$pair_test_dir/rate"
# 与项目原测试一致，保留地址及未定义行为检查，关闭环境不支持的泄漏扫描。
ASAN_OPTIONS=detect_leaks=0 "$pair_test_dir/rate"
g++ -std=c++17 -Wall -Wextra -Werror research/rrzc/tests/test_rocksdb_observe_plan.cc -o "$pair_test_dir/plan"
"$pair_test_dir/plan"
python3 -m unittest discover -s research/rrzc/tests -p 'test_pair_rate_analysis.py'
