#!/usr/bin/env bash
# 只提取真实函数做内存回归；旧缺陷有界退出，不访问设备、不启动客机。
set -euo pipefail
cd "$(dirname "$0")/../../.."
reopen_test_dir=$(mktemp -d /tmp/rrzc-reopen-tests.XXXXXX)
printf '重开锁序构建目录：%s\n' "$reopen_test_dir"
mkdir "$reopen_test_dir/old" "$reopen_test_dir/fixed"
for variant in old fixed; do
  source_file=build-femu/rrzc/tail-ablation-work/fs/fs_zenfs.cc
  if [[ $variant == fixed ]]; then source_file=build-femu/rrzc/reopen-lock-work/fs/fs_zenfs.cc; fi
  awk '/^IOStatus ZenFS::OpenWritableFile\(/ { on=1 } on { print } on && /^}$/ { on=0 }' \
    "$source_file" > "$reopen_test_dir/$variant/reopen-under-test.inc"
  test -s "$reopen_test_dir/$variant/reopen-under-test.inc"
  g++ -std=c++20 -Wall -Wextra -Werror -pthread -fsanitize=address,undefined \
    -I "$reopen_test_dir/$variant" research/rrzc/tests/test_reopen_lock.cc -o "$reopen_test_dir/$variant/test"
done
ASAN_OPTIONS=detect_leaks=0 timeout 15s "$reopen_test_dir/old/test" --expect-old
ASAN_OPTIONS=detect_leaks=0 timeout 15s "$reopen_test_dir/fixed/test"
