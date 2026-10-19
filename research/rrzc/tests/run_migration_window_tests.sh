#!/usr/bin/env bash
# 只编译内存测试并验证合成记录，不启动客机，不打开块设备。
set -euo pipefail
cd "$(dirname "$0")/../../.."
migration_test_dir=$(mktemp -d /tmp/rrzc-migration-window.XXXXXX)
printf '迁移窗口测试目录：%s\n' "$migration_test_dir"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -Iresearch/rrzc/model \
  -fsanitize=address,undefined research/rrzc/tests/test_migration_window.cc -o "$migration_test_dir/window"
ASAN_OPTIONS=detect_leaks=0 "$migration_test_dir/window"
python3 -m py_compile research/rrzc/validity_migration_window.py research/rrzc/tests/test_validity_migration_window.py
python3 -m unittest discover -s research/rrzc/tests -p 'test_validity_migration_window.py'
