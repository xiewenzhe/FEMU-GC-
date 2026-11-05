#!/usr/bin/env bash
# 仅新构建子镜像编译统一入口，旧收益预算接线和默认行为保留。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$(dirname "$(readlink -f "$0")")
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
mkdir "$target/before"
cp plugin/zenfs/fs/fs_zenfs.cc "$target/before/fs_zenfs.cc"
cp "$directory/ycsb/ycsb" "$target/before/ycsb"
cd plugin/zenfs
[[ ! -e fs/rrzc_direct_selector.h && ! -e fs/rrzc_direct_zenfs.inc ]]
cp "$target/rrzc_direct_selector.h" "$target/rrzc_direct_zenfs.inc" fs/
cp fs/rrzc_weighted_selector.h "$target/before/"
cp "$target/rrzc_weighted_selector.h" fs/
# 补丁有单行上下文；完整父清单已先核验，再允许小上下文并检查反向应用。
git apply --unidiff-zero --check "$target/zenfs-direct-selector.patch"
git apply --unidiff-zero "$target/zenfs-direct-selector.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-direct-selector.patch"
cp fs/zbd_zenfs.cc fs/zbd_zenfs.h "$target/before/"
git apply --unidiff-zero --check "$target/zenfs-direct-spare-observe.patch"
git apply --unidiff-zero "$target/zenfs-direct-spare-observe.patch"
git apply --unidiff-zero --reverse --check "$target/zenfs-direct-spare-observe.patch"
cp fs/zbd_zenfs.cc fs/zbd_zenfs.h "$target/"
cp fs/io_zenfs.cc "$target/before/"
cp "$target/rrzc_zone_content.h" fs/
git apply --check "$target/zenfs-content-guard.patch"
git apply "$target/zenfs-content-guard.patch"
git apply --reverse --check "$target/zenfs-content-guard.patch"
cp fs/zbd_zenfs.cc fs/zbd_zenfs.h fs/io_zenfs.cc "$target/"
cp fs/fs_zenfs.cc "$target/fs_zenfs.compiled.cc"
g++ -std=c++17 -Wall -Wextra -Werror -Ifs "$target/test_direct_selector.cc" -o "$target/model-unit"
"$target/model-unit" > "$target/model-unit.log" 2>&1
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$target/library-build.log" 2>&1
cd "$directory/ycsb"
make -W core/db_factory.o -j4 BIND_ROCKSDB=1 EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
cd /home/femu/rocksdb
for program in verify_ycsb benefit_device_smoke direct_spare_smoke content_guard_smoke reset_generation_probe; do
  source=$target/$program.cc
  output=$target/$program
  [[ $program != verify_ycsb ]] || { source=$directory/verify_ycsb.cc; output=$directory/verify_ycsb; }
  g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
    "$source" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
    -o "$output" > "$target/$program-build.log" 2>&1
done
for arm in before first second; do
  mkdir "$target/selftest-$arm"
  binary=$directory/ycsb/ycsb
  [[ $arm != before ]] || binary=$target/before/ycsb
  env -i PATH=/usr/bin:/bin LANG=C.UTF-8 YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$binary" -load -run -db rocksdb -threads 1 -P "$target/workload-locality-wide-zns.properties" \
    -p recordcount=1024 -p rrzc.read_key_count=64 -p rrzc.update_key_start=64 -p rrzc.update_key_count=192 \
    -p operationcount=8192 -p rocksdb.fs_uri= -p rocksdb.dbname="$target/selftest-$arm/db" \
    -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
done
for arm in first second; do
  cmp "$target/selftest-before/audit.requests" "$target/selftest-$arm/audit.requests"
  cmp "$target/selftest-before/audit.expected" "$target/selftest-$arm/audit.expected"
done
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
python3 - "$target" "$directory" <<'PY'
from pathlib import Path
import sys
target=Path(sys.argv[1]);directory=sys.argv[2]
allowed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/zbd_zenfs.cc','plugin/zenfs/fs/zbd_zenfs.h','plugin/zenfs/fs/io_zenfs.cc',
    'plugin/zenfs/fs/rrzc_weighted_selector.h',
    'librocksdb_debug.a',directory+'/ycsb/ycsb',directory+'/verify_ycsb'}
rows=(target/'parent.software.sha256').read_text().splitlines()
(target/'unchanged.sha256').write_text(''.join(r+'\n' for r in rows if r.split(maxsplit=1)[1] not in allowed))
PY
sha256sum -c "$target/unchanged.sha256" > "$target/software.unchanged.log"
cp "$target/unchanged.sha256" "$target/software.sha256"
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h \
  plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/rrzc_zone_content.h \
  plugin/zenfs/fs/rrzc_direct_selector.h plugin/zenfs/fs/rrzc_direct_zenfs.inc \
  plugin/zenfs/fs/rrzc_weighted_selector.h \
  librocksdb_debug.a "$directory/ycsb/ycsb" "$directory/verify_ycsb" "$target/reset_generation_probe" \
  "$target/benefit_device_smoke" "$target/direct_spare_smoke" "$target/content_guard_smoke" >> "$target/software.sha256"
