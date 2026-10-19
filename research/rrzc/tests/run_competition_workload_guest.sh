#!/usr/bin/env bash
# 仅新隔离客机：180秒三候选竞争验证，不评价性能收益。
set -euo pipefail
directory=/home/femu/rrzc-model-competition-20261015-greedy-tests
result=/home/femu/rrzc-stream-results/model-competition-20261015-greedy
program=$directory/competition_workload_test
trap 'code=$?; printf "%s\n" "$code" > "$directory/launcher.exit"; touch "$directory/finished"' EXIT
[[ $# == 0 && $(id -u) == 0 && -x $program && $(cat "$directory/build.exit") == 0 ]]
[[ ! -e $result && ! -e $result-aux && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-stream-results
mkdir "$result" "$result-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;r=json.load(sys.stdin);z=r["zone_list"];assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 for v in z), "非全空设备，拒绝初始化"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/io_zenfs.cc plugin/zenfs/fs/io_zenfs.h
  plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h
  plugin/zenfs/fs/rrzc_*.h librocksdb_debug.a plugin/zenfs/util/zenfs "$program"
  "$directory/competition_workload_test.cc" "$directory/competition_workload_plan.h" "$directory/run_competition_workload_guest.sh")
sha256sum "${identity[@]}" > "$result/identity.initial.sha256"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$result/scheduler.log"
status=0
# 只初始化本轮已确认全空的易失模拟NVMe，不操作OS盘或旧客机。
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result-aux" --enable_gc > "$result/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/mkfs.exit"
[[ $status == 0 ]]
printf '用途,计划业务秒数,RR阈值,GC启动比例,回收预算字节,额外预算字节,紧迫界限秒数,策略\n三候选竞争验证,180,4096,20,201326592,16777216,10,greedy\n' > "$result/config.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
  -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_ENABLE -u RRZC_TEST_VICTIM_PROFILE -u ZENFS_RRZC_TAIL_ABLATION \
  -u ZENFS_RRZC_V1_GROWTH -u ZENFS_RRZC_SHORT_SHADOW -u ZENFS_RRZC_COST_OBSERVE \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_GUARDED_MODE=shadow RRZC_TEST_MODEL_PROFILE=competition RRZC_TEST_RECENT_SAMPLE_MS=500 \
  python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(64<<20,64<<20));os.execv(sys.argv[1],sys.argv[1:])' \
  "$program" run 180 "$result" > "$result/run.stdout.log" 2>&1 &
worker=$!
printf '%s\n' "$worker" > "$result/worker.pid"
wait "$worker" || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    -u ZENFS_RRZC_RECLAIM_BYTES -u ZENFS_RRZC_SPACE_WEIGHT_PCT -u ZENFS_RRZC_V1_ENABLE \
    -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_GROWTH -u ZENFS_RRZC_SHORT_SHADOW -u ZENFS_RRZC_COST_OBSERVE \
    -u RRZC_TEST_RECENT_SAMPLE_MS -u ZENFS_RRZC_GUARDED_MODE -u ZENFS_RRZC_TAIL_ABLATION \
    -u RRZC_TEST_VICTIM_PROFILE ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
    ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=0 RRZC_TEST_MODEL_PROFILE=competition \
    "$program" verify 180 "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum "${identity[@]}" > "$result/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$directory/logs.sha256.tmp"
mv "$directory/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
