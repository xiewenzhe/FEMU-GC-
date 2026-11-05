#!/usr/bin/env bash
# 仅在新子镜像编译候选范围实验；模型、严格RR及旧程序身份不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/build.finished && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc "$target/before/"
[[ ! -e plugin/zenfs/fs/rrzc_candidate_pool.h ]]
cp "$target/rrzc_candidate_pool.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-candidate-pool.patch"
git apply --unidiff-zero "$target/zenfs-candidate-pool.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-candidate-pool.patch"
cp fs/fs_zenfs.cc "$target/fs_zenfs.after.cc"
cd "$target"
cp joint_opportunity_smoke.cc before/
git apply --unidiff-zero --check candidate-pool-client.patch
git apply --unidiff-zero candidate-pool-client.patch
git apply --unidiff-zero --reverse --check candidate-pool-client.patch
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_candidate_pool.cc" -o "$target/model-unit" > "$target/unit-build.log" 2>&1
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -DRRZC_JOINT_TRACE_AVAILABLE -Iplugin/zenfs/fs -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/candidate_pool_smoke" > "$target/program-build.log" 2>&1
python3 - "$target" <<'PY'
from pathlib import Path
import hashlib,sys
t=Path(sys.argv[1]);raw=(t/'candidate_pool_smoke').read_bytes()
for marker in ('ZENFS_RRZC_CANDIDATE_POOL','候选范围资格：','ZENFS_RR_STRICT','期望RR成本：'):
    assert marker.encode() in raw,'新程序没有链接实验分支：'+marker
old={n:d for d,n in (line.split(maxsplit=1) for line in (t/'parent.software.sha256').read_text().splitlines())}
changed={'plugin/zenfs/fs/fs_zenfs.cc','librocksdb_debug.a'}
extra=['plugin/zenfs/fs/rrzc_candidate_pool.h',str(t/'candidate_pool_smoke')]
extra += [str(p) for p in t.iterdir() if p.is_file() and p.suffix in ('.sh','.py','.cc','.h','.patch')]
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
actual={n:digest(n) for n in list(old)+extra}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'旧模型、RR路径或旧客户端变化'
assert actual['librocksdb_debug.a']!=old['librocksdb_debug.a'],'新库未确认重新编译'
with (t/'software.sha256').open('x') as stream:
    for n,d in actual.items():stream.write(d+'  '+n+'\n')
PY
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
