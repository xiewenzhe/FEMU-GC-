#!/usr/bin/env bash
# 只在本轮隔离构建镜像中应用默认关闭的公共空替补补丁，先编译和普通目录校验。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$directory/migration-spare-20261019
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
[[ $# == 0 && $(id -un) == femu && ! -e $target/before ]]
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256"
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir "$target/before"
cp plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h "$target/before/"
cd plugin/zenfs
git apply --check "$target/zenfs-migration-spare.patch"
git apply "$target/zenfs-migration-spare.patch"
git apply --reverse --check "$target/zenfs-migration-spare.patch"
cd /home/femu/rocksdb
make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench > "$target/library-build.log" 2>&1
cd "$directory/ycsb"
# 重新编译绑定并链接新静态库，避免上游Makefile漏掉外部库依赖。
make -W core/db_factory.cc -W rocksdb/rocksdb_db.cc -j4 BIND_ROCKSDB=1 \
  EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
cd /home/femu/rocksdb
for name in verify_ycsb migration_spare_probe; do
  source=$target/$name.cc
  output=$target/$name
  if [[ $name == verify_ycsb ]]; then source=$directory/$name.cc; output=$directory/$name; fi
  g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
    "$source" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
    -o "$output" > "$target/$name-build.log" 2>&1
done
for arm in first second; do
  mkdir "$target/selftest-$arm"
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$target/selftest-$arm/audit" \
    "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$target/workload-independent-zns.properties" \
    -p recordcount=1024 -p operationcount=8192 -p rrzc.read_key_count=64 -p fieldlength=128 \
    -p rocksdb.fs_uri= -p rocksdb.dbname="$target/selftest-$arm/db" \
    -p limit.ops=0 -p sleepafterload=0 -p status.interval=1 > "$target/selftest-$arm/ycsb.log" 2>&1
  "$directory/verify_ycsb" posix "$target/selftest-$arm/db" "$target/selftest-$arm/audit.expected" \
    > "$target/selftest-$arm/verify.log" 2>&1
done
cmp "$target/selftest-first/audit.requests" "$target/selftest-second/audit.requests"
cmp "$target/selftest-first/audit.expected" "$target/selftest-second/audit.expected"
printf '0\n' > "$target/selftest.exit"
cd /home/femu/rocksdb
# 保留父身份清单的每一项，新增测试入口另记，不删除未变化项。
while read -r checksum name; do sha256sum "$name"; done < "$target/parent.software.sha256" > "$target/software.sha256"
sha256sum "$target/migration_spare_probe" "$target/run_migration_spare_probe_guest.sh" \
  "$target/run_ycsb_migration_spare_guest.sh" "$target/workload-independent-zns.properties" >> "$target/software.sha256"
