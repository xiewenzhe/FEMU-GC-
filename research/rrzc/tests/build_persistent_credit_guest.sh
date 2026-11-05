#!/usr/bin/env bash
# 仅在全新子镜像部署持续读修正；保留旧评分核、客户端和修改前接线。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
# 父清单未包含该旧头；只比对不覆盖，防止宿主同块校验与实际客机版本不一致。
cmp "$target/rrzc_v1_growth.h" plugin/zenfs/fs/rrzc_v1_growth.h
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/rrzc_direct_zenfs.inc "$target/before/"
[[ ! -e plugin/zenfs/fs/rrzc_persistent_credit_selector.h ]]
cp "$target/rrzc_persistent_credit_selector.h" plugin/zenfs/fs/
cd plugin/zenfs
git apply --unidiff-zero --check "$target/zenfs-persistent-credit.patch"
git apply --unidiff-zero "$target/zenfs-persistent-credit.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-persistent-credit.patch"
cp fs/fs_zenfs.cc fs/rrzc_direct_zenfs.inc "$target/"
cd /home/femu/rocksdb
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -Iplugin/zenfs/fs \
  "$target/test_persistent_credit_selector.cc" -o "$target/model-unit"
"$target/model-unit" > "$target/model-unit.log" 2>&1
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench > "$target/library-build.log" 2>&1
g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  "$target/joint_opportunity_smoke.cc" librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o "$target/joint_opportunity_smoke" > "$target/program-build.log" 2>&1
# 中文UTF-8不能由默认ASCII strings可靠提取；按原始字节检查，不把标记当执行证据。
python3 - "$target" <<'PY'
from pathlib import Path
import sys
t=Path(sys.argv[1]);raw=(t/'joint_opportunity_smoke').read_bytes()
assert b'ZENFS_RRZC_PERSISTENT_CREDIT' in raw,'新开关未链接进程序'
assert '持续读收益成本：'.encode('utf-8') in raw,'新模型日志标记未链接进程序'
(t/'执行分支.log').write_text('新开关及UTF-8日志字节存在；实际执行仍需运行日志核验\n')
PY
# 全量继承父清单：只更新两份接线及库，旧模型头和旧客户端仍必须一致。
python3 - "$target" <<'PY'
from pathlib import Path
import hashlib,sys
t=Path(sys.argv[1]);old={n:d for d,n in (line.split(maxsplit=1) for line in (t/'parent.software.sha256').read_text().splitlines())}
changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
extra=['plugin/zenfs/fs/rrzc_persistent_credit_selector.h','plugin/zenfs/fs/rrzc_v1_growth.h',str(t/'joint_opportunity_smoke'),
       str(t/'run_persistent_credit_guest.sh'),str(t/'build_persistent_credit_guest.sh'),
       str(t/'joint_opportunity_smoke.cc'),str(t/'v1_execution_smoke_test.cc'),str(t/'test_persistent_credit_selector.cc')]
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
actual={n:digest(n) for n in list(old)+extra}
assert all(actual[n]==d for n,d in old.items() if n not in changed),'旧评分核或旧客户端意外变化'
assert actual['librocksdb_debug.a']!=old['librocksdb_debug.a'],'未确认新库重编译'
with (t/'software.sha256').open('x') as stream:
    for n,d in actual.items():stream.write(d+'  '+n+'\n')
PY
sha256sum -c "$target/software.sha256" > "$target/software.built.log"
