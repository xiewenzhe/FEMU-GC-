#!/usr/bin/env bash
# 仅编译并执行内存测试，不启动客机，不访问块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
sampler_test_dir=$(mktemp -d /tmp/rrzc-recent-tests.XXXXXX)
printf '独立采样测试目录：%s\n' "$sampler_test_dir"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -Iresearch/rrzc/model \
  -fsanitize=address,undefined research/rrzc/tests/test_recent_sampler.cc -o "$sampler_test_dir/sampler"
# 受追踪环境不支持泄漏扫描；保留地址和未定义行为检查。
ASAN_OPTIONS=detect_leaks=0 "$sampler_test_dir/sampler"
g++ -std=c++17 -Wall -Wextra -Werror research/rrzc/tests/test_rocksdb_observe_plan.cc -o "$sampler_test_dir/plan"
"$sampler_test_dir/plan"
python3 -m unittest discover -s research/rrzc/tests -p 'test_rocksdb_recent.py'
