#!/usr/bin/env bash
# 复用已验收的客户端重链接/普通目录自测，额外验证新模型与不限速运行载荷。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/stable.build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/stable.build.exit"; touch "$target/stable.build.finished"' EXIT
bash "$target/build_sst_probe_guest.sh"
for symbol in ZENFS_RRZC_STABLE_CREDIT ZENFS_RRZC_PAIR_GREEDY; do
  strings "$target/ycsb/ycsb" | grep -F "$symbol" > "$target/$symbol.log"
done
# 原库和旧客户端不变；新增软件身份只覆盖本轮新文件，不改历史清单。
sha256sum "$target/build_stable_ycsb_guest.sh" "$target/run_stable_ycsb_guest.sh" \
  "$target/workload-stable-unlimited.properties" >> "$target/software.sha256"
