#!/usr/bin/env bash
# 固定原请求，只增加迁移窗口观测；只允许本轮全新全空隔离设备。
set -euo pipefail
directory=/home/femu/rrzc-rocksdb-migration-observe-20261017-tests
result=/home/femu/rrzc-stream-results/rocksdb-anchor-slow-20261016
program=$directory/rocksdb_observe_test
[[ $# == 0 && ! -e $directory/finished ]]
trap 'code=$?; printf "%s\n" "$code" > "$directory/launcher.exit"; touch "$directory/finished"' EXIT
[[ $(id -u) == 0 && -x $program && $(cat "$directory/build.exit") == 0 && $(cat "$directory/selftest.exit") == 0 ]]
[[ ! -e $result && ! -e $result-aux && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$result" "$result-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;r=json.load(sys.stdin);z=r["zone_list"];assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 for v in z), "不是新全空设备"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a "$program" "$directory/rocksdb_observe_test.cc" "$directory/rocksdb_observe_plan.h"
  "$directory/test_rocksdb_observe_plan.cc" "$directory/test_rocksdb_anchor_plan.cc" "$directory/run_migration_observe_guest.sh")
sha256sum "${identity[@]}" > "$result/identity.initial.sha256"
# 此处仅格式化已核验的本轮全空虚拟设备；从不打开原镜像或旧客机。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$result/scheduler.log"
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result-aux" --enable_gc > "$result/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/mkfs.exit"
[[ $status == 0 ]]
printf '策略,混合业务秒数,初始键数,值字节,覆盖写键数,读次数,RR阈值,GC启动比例,额外预算字节,覆盖写秒数,独立近期采样,双点速率影子,条带数,每条带锚点键数,可写键域大小,锚点热读起始秒,覆盖写速率键每秒,读业务秒数,迁移窗口观测\nGreedy影子观察,180,655360,4096,737280,48000,4096,20,16777216,180,1,0,4,8192,229376,45,4096,120,1\n' > "$result/config.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
  -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_ENABLE -u ZENFS_RRZC_V1_GROWTH -u ZENFS_RRZC_SHORT_SHADOW \
  -u ZENFS_RRZC_COST_OBSERVE -u ZENFS_RRZC_TAIL_ABLATION -u ZENFS_RRZC_PAIR_RATE_SHADOW \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  RRZC_TEST_DB_PROFILE=anchors180slow ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 ZENFS_RRZC_MIGRATION_WINDOW=1 \
  python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(128<<20,128<<20));os.execv(sys.argv[1],sys.argv[1:])' \
  "$program" run "$result" > "$result/run.stdout.log" 2>&1 &
worker=$!
printf '%s\n' "$worker" > "$result/worker.pid"
wait "$worker" || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    -u ZENFS_RRZC_RECLAIM_BYTES -u ZENFS_RRZC_SPACE_WEIGHT_PCT -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_ENABLE \
    -u ZENFS_RRZC_V1_GROWTH -u ZENFS_RRZC_SHORT_SHADOW -u ZENFS_RRZC_PAIR_RATE_SHADOW -u ZENFS_RRZC_RECENT_SAMPLER \
    -u ZENFS_RRZC_COST_OBSERVE -u ZENFS_RRZC_GUARDED_MODE -u ZENFS_RRZC_COST_RANK -u ZENFS_RRZC_TAIL_ABLATION -u ZENFS_RRZC_MIGRATION_WINDOW \
    RRZC_TEST_DB_PROFILE=anchors180slow ZENFS_RR_ENABLE=0 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 \
    "$program" verify "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum "${identity[@]}" > "$result/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$directory/logs.sha256.tmp"
mv "$directory/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
