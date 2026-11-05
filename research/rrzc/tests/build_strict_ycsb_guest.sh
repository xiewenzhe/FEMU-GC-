#!/usr/bin/env bash
# 重链接严格RR库；两臂同一静态库和相同显式读取协议。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/window.build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/window.build.exit"; touch "$target/window.build.finished"' EXIT
bash "$target/build_window_client_guest.sh"
# 直接验证UTF-8字节和新开关；标记存在不等于已执行，仍需业务日志独立核验。
python3 - "$target" <<'PY'
from pathlib import Path
import sys
t=Path(sys.argv[1]);raw=(t/'ycsb/ycsb').read_bytes()
for text in ('ZENFS_RRZC_WINDOW_CREDIT','ZENFS_RRZC_PAIR_GREEDY','窗口收益模式：',
             'ZENFS_RRZC_PERSISTENT_CREDIT','ZENFS_RRZC_EXPECTED_RR','期望RR配置：','期望RR成本：','ZENFS_RR_STRICT','严格RR主机配置：'):
    assert text.encode('utf-8') in raw,'新YCSB缺少模型标记：'+text
(t/'窗口链接核验.log').write_text('新开关及UTF-8日志字节存在；真实执行由评分日志证明\n')
PY
cd /home/femu/rocksdb
sha256sum "$target/build_strict_ycsb_guest.sh" "$target/run_strict_ycsb_guest.sh" "$target/strict_stats_guest.py" \
  "$target/workload-stable-unlimited.properties" >> "$target/software.sha256"
sha256sum -c "$target/software.sha256" > "$target/software.window.log"
