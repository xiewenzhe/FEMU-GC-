#!/usr/bin/env bash
# 只在新子镜像接线期望RR实验核；旧评分头、RR路径、旧程序保持不变。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
cmp "$target/rrzc_recent_sampler.h" plugin/zenfs/fs/rrzc_recent_sampler.h
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc "$target/before/"
[[ ! -e plugin/zenfs/fs/rrzc_expected_rr_selector.h ]]
cp "$target/rrzc_expected_rr_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-expected-rr.patch"
git apply --unidiff-zero "$target/zenfs-expected-rr.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-expected-rr.patch"
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_expected_rr_selector.cc" -o "$target/model-unit" > "$target/unit-build.log" 2>&1
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX \
  -DRRZC_JOINT_TRACE_AVAILABLE -Iplugin/zenfs/fs -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
# UTF-8标记仅确认链接；实际执行仍必须通过业务日志独立核验。
python3 - "$target" <<'PY'
from pathlib import Path
import hashlib,sys
t=Path(sys.argv[1]);raw=(t/'joint_opportunity_smoke').read_bytes()
for marker in ('ZENFS_RRZC_EXPECTED_RR','期望RR成本：','块轨迹对时：'):
    assert marker.encode() in raw,'实际链接程序缺少分支：'+marker
old={n:d for d,n in (line.split(maxsplit=1) for line in (t/'parent.software.sha256').read_text().splitlines())}
changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
extra=['plugin/zenfs/fs/rrzc_expected_rr_selector.h',str(t/'joint_opportunity_smoke')]
extra += [str(t/n) for n in ('build_expected_rr_guest.sh','run_expected_rr_guest.sh','joint_opportunity_smoke.cc',
    'v1_execution_smoke_test.cc','joint_read_trace.h','rrzc_recent_sampler.h','test_expected_rr_selector.cc')]
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
actual={n:digest(n) for n in list(old)+extra}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'旧评分、RR或旧客户端身份变化'
assert actual['librocksdb_debug.a']!=old['librocksdb_debug.a'],'新库没有确认重编译'
with (t/'software.sha256').open('x') as stream:
    for n,d in actual.items():stream.write(d+'  '+n+'\n')
(t/'执行分支.log').write_text('新分支及轨迹字节已链接；此记录不是选区执行或收益证明\n')
PY
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
