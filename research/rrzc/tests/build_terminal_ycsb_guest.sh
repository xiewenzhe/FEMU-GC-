#!/usr/bin/env bash
# 仅在独立子镜像中将公开派生YCSB重链接到已验收的第二版库；不再编译或修改库。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/window.build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/window.build.exit"; touch "$target/window.build.finished"' EXIT
bash "$target/build_window_client_guest.sh"
python3 - "$target" <<'PY'
from pathlib import Path
import sys
t=Path(sys.argv[1]);raw=(t/'ycsb/ycsb').read_bytes()
for marker in ('ZENFS_RRZC_JOINT_TIME','ZENFS_RRZC_TERMINAL','边界配置：版本=2，',
               '时序方案决策：','边界方案决策：','ZENFS_RR_STRICT'):
    assert marker.encode() in raw,'静态重链接分支缺失：'+marker
(t/'窗口链接核验.log').write_text('新开关及UTF-8日志字节存在；第二版实际执行仍须独立重算\n')
PY
cd /home/femu/rocksdb
sha256sum "$target/build_terminal_ycsb_guest.sh" "$target/run_terminal_ycsb_guest.sh" \
  "$target/strict_stats_guest.py" "$target/workload-stable-unlimited.properties" >> "$target/software.sha256"
sha256sum -c "$target/software.sha256" > "$target/software.final.log"
