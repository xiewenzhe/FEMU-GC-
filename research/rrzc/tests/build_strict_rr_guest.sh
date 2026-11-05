#!/usr/bin/env bash
# 隔离子镜像安装严格RR接线，重编译库；原模型头和旧客户端保持不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
for name in fs_zenfs.cc zbd_zenfs.h zbdlib_zenfs.cc io_zenfs.h io_zenfs.cc; do
  cp "plugin/zenfs/fs/$name" "$target/before/"
done
cp "$target/rrzc_strict_io.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --check "$target/zenfs-strict-rr.patch"
git apply "$target/zenfs-strict-rr.patch"
git apply --reverse --check "$target/zenfs-strict-rr.patch"
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
python3 - "$target" <<'PY'
import hashlib,sys
from pathlib import Path
t=Path(sys.argv[1])
old={n:d for d,n in (s.split(maxsplit=1) for s in (t/'parent.software.sha256').read_text().splitlines())}
changed={'librocksdb_debug.a',*('plugin/zenfs/fs/'+n for n in ('fs_zenfs.cc','zbd_zenfs.h','zbdlib_zenfs.cc','io_zenfs.h','io_zenfs.cc'))}
def digest(name):
    h=hashlib.sha256()
    with Path(name).open('rb') as f:
        while block:=f.read(1<<20):h.update(block)
    return h.hexdigest()
names=set(old)|changed|{'plugin/zenfs/fs/rrzc_strict_io.h'}
names|={str(t/n) for n in ('build_strict_rr_guest.sh','zenfs-strict-rr.patch','rrzc_strict_io.h')}
actual={n:digest(n) for n in sorted(names)}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'原模型或旧客户端意外变化'
assert actual['librocksdb_debug.a']!=old['librocksdb_debug.a'],'严格RR库未重编译'
with (t/'software.sha256').open('x') as f:
    for n,d in actual.items():f.write(d+'  '+n+'\n')
PY
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
