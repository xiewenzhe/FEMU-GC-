#!/usr/bin/env bash
# 只在新子镜像接入默认关闭的边界核；旧模型头、旧程序和RR路径不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/build.finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
python3 -c 'import os;s=os.statvfs(".");assert s.f_bavail*s.f_frsize>=6<<30,"根盘不足6GiB，不删文件或自动扩盘"'
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc "$target/before/"
[[ ! -e plugin/zenfs/fs/rrzc_terminal_selector.h ]]
cp "$target/rrzc_terminal_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --check "$target/zenfs-terminal.patch"
git apply "$target/zenfs-terminal.patch"
git apply --reverse --check "$target/zenfs-terminal.patch"
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_terminal_selector.cc" -o "$target/model-unit" > "$target/unit-build.log" 2>&1
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -DRRZC_JOINT_TRACE_AVAILABLE -Iplugin/zenfs/fs -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_time_smoke" > "$target/program-build.log" 2>&1
python3 - "$target" <<'PY'
from pathlib import Path
import hashlib,sys
t=Path(sys.argv[1]);raw=(t/'joint_time_smoke').read_bytes()
for marker in ('ZENFS_RRZC_TERMINAL','边界方案决策：','ZENFS_RR_STRICT','时序方案决策：'):
    assert marker.encode() in raw,'新旧接线或静态链接未确认：'+marker
old={n:d for d,n in (s.split(maxsplit=1) for s in (t/'parent.software.sha256').read_text().splitlines())}
changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
extra=['plugin/zenfs/fs/rrzc_terminal_selector.h',str(t/'joint_time_smoke')]
extra += [str(p) for p in t.iterdir() if p.is_file() and p.suffix in ('.sh','.py','.cc','.h','.patch')]
def digest(n):
    h=hashlib.sha256()
    with Path(n).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()
actual={n:digest(n) for n in [*old,*extra]}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'旧核、RR或旧程序改变'
assert all(actual[n]!=old[n] for n in changed),'新库或接线未更新'
with (t/'software.sha256').open('x') as f:
    for n,d in actual.items():f.write(d+'  '+n+'\n')
PY
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
