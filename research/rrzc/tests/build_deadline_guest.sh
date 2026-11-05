#!/usr/bin/env bash
# 独立编译时序模型及多帧增长；旧模型与严格RR代码不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/window.build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/window.build.exit"; touch "$target/window.build.finished"' EXIT
cd /home/femu/rocksdb
python3 - "$target" <<'PY'
import json,os,sys
from pathlib import Path
s=os.statvfs('/home/femu/rocksdb');free=s.f_bavail*s.f_frsize
(Path(sys.argv[1])/'构建空间.json').write_text(json.dumps({'根盘可用字节':free,'最低字节':6<<30},ensure_ascii=False))
assert free>=6<<30,'客机根盘空间不足；不删除或覆盖旧结果'
PY
sha256sum -c "$target/parent.software.sha256" > "$target/software.original.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc "$target/before/"
[[ ! -e plugin/zenfs/fs/rrzc_deadline_selector.h ]]
cp "$target/rrzc_deadline_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --check "$target/zenfs-deadline.patch"
git apply "$target/zenfs-deadline.patch"
git apply --reverse --check "$target/zenfs-deadline.patch"
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_deadline_selector.cc" -o "$target/model-unit" > "$target/model-unit-build.log" 2>&1
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
# 冻结新库后让既有客户端构建器只重链接；原清单另存，不覆盖任何原始证据。
mv "$target/parent.software.sha256" "$target/parent.original.sha256"
python3 - "$target" <<'PY'
from pathlib import Path
import hashlib,sys
t=Path(sys.argv[1]);old={n:d for d,n in (r.split(maxsplit=1) for r in (t/'parent.original.sha256').read_text().splitlines())}
changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
def digest(n):
    h=hashlib.sha256()
    with Path(n).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
actual={n:digest(n) for n in [*old,'plugin/zenfs/fs/rrzc_deadline_selector.h']}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'旧预测核、RR或旧客户端改变'
assert all(actual[n]!=old[n] for n in changed),'新接线或库未重新构建'
with (t/'parent.software.sha256').open('x') as f:
    for n,d in actual.items():f.write(d+'  '+n+'\n')
PY
bash "$target/build_window_client_guest.sh"
python3 - "$target" <<'PY'
from pathlib import Path
import sys
t=Path(sys.argv[1]);raw=(t/'ycsb/ycsb').read_bytes()
for marker in ('ZENFS_RRZC_DEADLINE','时序方案候选：','时序方案决策：','ZENFS_RR_STRICT','期望RR成本：'):
    assert marker.encode() in raw,'静态重链接分支缺失：'+marker
(t/'窗口链接核验.log').write_text('新开关及UTF-8日志字节存在；实际执行须独立重算\n')
PY
sha256sum "$target/build_deadline_guest.sh" "$target/run_deadline_guest.sh" \
  "$target/strict_stats_guest.py" "$target/workload-stable-unlimited.properties" \
  "$target/zenfs-deadline.patch" "$target/test_deadline_selector.cc" >> "$target/software.sha256"
sha256sum -c "$target/software.sha256" > "$target/software.final.log"
