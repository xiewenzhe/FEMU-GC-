#!/usr/bin/env bash
# 同库限速配置准备及普通目录回归；不修改或编译库，不打开NVMe。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/paced-20261017
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir "$target/before"
cp "$target/parent.software.sha256" "$target/before/"
# 已完成真实编译及边界回归的停止父镜像；本轮复用同一二进制，不另改公共逻辑。
grep -Fxq 'limit.ops=1000' "$target/workloada-zns-nowal-paced.properties"
for arm in default nowal-first nowal-second; do
  mkdir "$target/selftest-$arm"
  extra=()
  [[ $arm == default ]] || extra=(-p rocksdb.disable_wal=true)
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$directory/ycsb/workloads/workloada" \
    -p recordcount=1024 -p operationcount=4096 -p fieldcount=1 -p fieldlength=128 -p writeallfields=true \
    -p rocksdb.dbname="$target/selftest-$arm/db" -p rocksdb.no_block_cache=true -p status=true -p status.interval=1 \
    "${extra[@]}" > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
done
grep -Fq '实验WAL配置：关闭=0' "$target/selftest-default/ycsb.log"
for arm in nowal-first nowal-second; do
  grep -Fq '实验WAL配置：关闭=1' "$target/selftest-$arm/ycsb.log"
  cmp "$target/selftest-default/audit.requests" "$target/selftest-$arm/audit.requests"
  cmp "$target/selftest-default/audit.expected" "$target/selftest-$arm/audit.expected"
done
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h \
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h \
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$directory/ycsb/ycsb" "$directory/verify_ycsb" \
  "$directory/ycsb/rocksdb/rocksdb_db.cc" "$target/run_ycsb_paced_guest.sh" \
  "$target/workloada-zns-nowal-paced.properties" > "$target/software.sha256"
