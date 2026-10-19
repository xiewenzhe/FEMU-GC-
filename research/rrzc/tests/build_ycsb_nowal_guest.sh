#!/usr/bin/env bash
# 仅新子镜像新增YCSB无WAL配置支持，不重编或修改ZenFS、FEMU。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
nowal=$directory/nowal-20261017
trap 'code=$?; printf "%s\n" "$code" > "$nowal/build.exit"; touch "$nowal/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $nowal/before ]]
cd /home/femu/rocksdb
sha256sum -c "$nowal/parent.software.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir "$nowal/before"
cp "$directory/ycsb/rocksdb/rocksdb_db.cc" "$nowal/before/"
cd "$directory/ycsb"
[[ $(sha256sum rocksdb/rocksdb_db.cc | cut -d' ' -f1) == 0d11587e115a7618e999458a9cda3424cc6bb67d2eea898ab95262f0240e5bc1 ]]
git apply --check "$nowal/ycsb-nowal.patch"
git apply "$nowal/ycsb-nowal.patch"
git apply --reverse --check "$nowal/ycsb-nowal.patch"
make -j4 BIND_ROCKSDB=1 EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$nowal/ycsb-build.log" 2>&1
for arm in default nowal-first nowal-second; do
  mkdir "$nowal/selftest-$arm"
  extra=()
  [[ $arm == default ]] || extra=(-p rocksdb.disable_wal=true)
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$nowal/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P workloads/workloada \
    -p recordcount=1024 -p operationcount=4096 -p fieldcount=1 -p fieldlength=128 -p writeallfields=true \
    -p rocksdb.dbname="$nowal/selftest-$arm/db" -p rocksdb.no_block_cache=true -p status=true -p status.interval=1 \
    "${extra[@]}" > "$nowal/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$nowal/selftest-$arm/db" "$nowal/selftest-$arm/audit.expected" \
    > "$nowal/selftest-$arm/verify.log" 2>&1
done
grep -Fq '实验WAL配置：关闭=0' "$nowal/selftest-default/ycsb.log"
for arm in nowal-first nowal-second; do
  grep -Fq '实验WAL配置：关闭=1' "$nowal/selftest-$arm/ycsb.log"
  cmp "$nowal/selftest-default/audit.requests" "$nowal/selftest-$arm/audit.requests"
  cmp "$nowal/selftest-default/audit.expected" "$nowal/selftest-$arm/audit.expected"
done
printf '0\n' > "$nowal/selftest.exit"
cd /home/femu/rocksdb
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$directory/ycsb/ycsb" "$directory/verify_ycsb" \
  "$directory/ycsb/rocksdb/rocksdb_db.cc" "$nowal/run_ycsb_nowal_guest.sh" \
  "$nowal/workloada-zns-nowal.properties" "$nowal/workloada-zns-nowal-pilot.properties" > "$nowal/software.sha256"
