#!/usr/bin/env bash
# 仅全新隔离客机：原180秒滚动计划，不修改生产选区或请求生成代码。
set -euo pipefail
[[ $# == 1 && $(id -u) == 0 ]]
strategy=$1
[[ $strategy == greedy || $strategy == v1 ]]
label=v1-growth-stream-$strategy-20261014
directory=/home/femu/rrzc-$label-tests
result=/home/femu/rrzc-stream-results/$label
program=$directory/stream_rrzc_test
[[ -x $program && $(cat "$directory/build.exit") == 0 && ! -e $result && ! -e $result-aux ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir -p /home/femu/rrzc-stream-results
mkdir "$result" "$result-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "设备不是32个完整空区，拒绝初始化"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc
  plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h
  plugin/zenfs/fs/rrzc_weighted_selector.h plugin/zenfs/fs/rrzc_recent_log.h
  plugin/zenfs/fs/rrzc_urgency_selector.h plugin/zenfs/fs/rrzc_v1_execution.h
  plugin/zenfs/fs/rrzc_v1_growth.h librocksdb_debug.a plugin/zenfs/util/zenfs "$program"
  "$directory/stream_rrzc_test.cc" "$directory/run_v1_growth_stream_guest.sh")
sha256sum "${identity[@]}" > "$result/identity.initial.sha256"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$result/scheduler.log"
status=0
# 只初始化本轮已核验全空的易失模拟NVMe，不操作OS盘或父镜像。
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result-aux" --enable_gc \
  > "$result/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/mkfs.exit"
[[ $status == 0 ]]
printf '策略,计划业务秒数,RR阈值,GC启动比例,回收预算字节,紧迫界限秒数,额外有效搬运预算字节,近期准入开关,观察采样毫秒,预热秒数\n%s,180,4096,20,201326592,10,16777216,1,500,0\n' "$strategy" > "$result/config.csv"
mode=(ZENFS_RRZC_V1_ENABLE=1)
if [[ $strategy == greedy ]]; then mode=(ZENFS_RRZC_V1_SHADOW=1); fi
# 普通文件限64MiB；直接块设备I/O不受此上限影响，避免失控日志填满OS盘。
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
  -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_ENABLE -u RRZC_TEST_VICTIM_PROFILE \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_V1_GROWTH=1 RRZC_TEST_RECENT_SAMPLE_MS=500 "${mode[@]}" \
  python3 -c 'import os,resource,sys; resource.setrlimit(resource.RLIMIT_FSIZE,(64<<20,64<<20)); os.execv(sys.argv[1],sys.argv[1:])' \
  "$program" run 180 "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    -u ZENFS_RRZC_RECLAIM_BYTES -u ZENFS_RRZC_SPACE_WEIGHT_PCT -u ZENFS_RRZC_V1_ENABLE \
    -u ZENFS_RRZC_V1_SHADOW -u ZENFS_RRZC_V1_GROWTH -u RRZC_TEST_RECENT_SAMPLE_MS \
    -u RRZC_TEST_VICTIM_PROFILE ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
    ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=0 \
    "$program" verify 180 "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
printf '%s\n' "$status" > "$result/runner.exit"
sha256sum "${identity[@]}" > "$result/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$result/logs.sha256"
cat "$result/run.stdout.log"
[[ ! -e $result/verify.stdout.log ]] || cat "$result/verify.stdout.log"
exit "$status"
