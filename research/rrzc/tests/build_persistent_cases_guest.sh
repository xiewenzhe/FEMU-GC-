#!/usr/bin/env bash
# 复用已编译同库，只在新目录编译区分场景；不改模型、接线、库或原客户端。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
cmp "$target/rrzc_recent_sampler.h" plugin/zenfs/fs/rrzc_recent_sampler.h
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_persistent_credit_selector.cc" -o "$target/model-unit" > "$target/unit-build.log" 2>&1
"$target/model-unit" > "$target/model-unit.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -DRRZC_JOINT_TRACE_AVAILABLE -Iplugin/zenfs/fs -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
python3 - "$target" <<'PY'
from pathlib import Path
import sys
t=Path(sys.argv[1]);raw=(t/'joint_opportunity_smoke').read_bytes()
for marker in ('burst-stopped','eligible-cost','ZENFS_RRZC_WINDOW_CREDIT','ZENFS_RRZC_PERSISTENT_CREDIT','持续读收益成本：'):
    assert marker.encode() in raw,'实际链接程序缺少场景或执行分支：'+marker
PY
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.after.log"
cp "$target/parent.software.sha256" "$target/software.sha256"
sha256sum "$target/joint_opportunity_smoke" "$target/joint_opportunity_smoke.cc" \
  "$target/v1_execution_smoke_test.cc" "$target/test_persistent_credit_selector.cc" \
  "$target/build_persistent_cases_guest.sh" "$target/run_persistent_credit_guest.sh" >> "$target/software.sha256"
sha256sum "$target/joint_read_trace.h" "$target/rrzc_recent_sampler.h" >> "$target/software.sha256"
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
