#!/usr/bin/env bash
# 原maintenance-cases请求与参数保持不变，仅使用独立名称避免覆盖继承结果。
set -euo pipefail
[[ $# == 0 && $(id -u) == 0 ]]
label=short-shadow-cases-20261014
sample_ms=500
directory=/home/femu/rrzc-$label-tests
result=/home/femu/rrzc-stream-results/$label
program=$directory/stream_rrzc_test
[[ -x $program && $(cat "$directory/build.exit") == 0 ]]
[[ ! -e $result && ! -e $result-aux ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-stream-results
mkdir "$result" "$result-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc
  plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h
  plugin/zenfs/fs/rrzc_weighted_selector.h plugin/zenfs/fs/rrzc_recent_log.h
  plugin/zenfs/fs/rrzc_urgency_selector.h plugin/zenfs/fs/rrzc_v1_execution.h
  plugin/zenfs/fs/rrzc_v1_growth.h plugin/zenfs/fs/rrzc_short_shadow.h
  librocksdb_debug.a plugin/zenfs/util/zenfs "$program"
  "$directory/stream_rrzc_test.cc" "$directory/run_short_shadow_cases_guest.sh")
sha256sum "${identity[@]}" > "$result/identity.initial.sha256"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$result/scheduler.log"
lscpu > "$result/cpu.log"
free -b > "$result/memory.initial.log"
status=0
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result-aux" --enable_gc \
  > "$result/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/mkfs.exit"
[[ $status == 0 ]]
printf '实际策略,计划业务秒数,RR阈值,GC启动比例,回收预算字节,影子界限秒数,额外有效搬运预算字节,实际改选\nGreedy,180,4096,20,201326592,10,16777216,0\n' > "$result/config.csv"
printf '采样间隔毫秒\n%s\n' "$sample_ms" > "$result/observer-config.csv"
export RRZC_TEST_RECENT_SAMPLE_MS=$sample_ms RRZC_TEST_VICTIM_PROFILE=maintenance-cases
printf '场景配置,热读停止年龄秒,提前退役年龄秒\nmaintenance-cases,65,66\n' > "$result/victim-profile.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 ZENFS_RRZC_V1_SHADOW=1 \
  "$program" run 180 "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  # 独立只读校验，不纳入业务或维护统计。
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    -u ZENFS_RRZC_SPACE_WEIGHT_PCT -u ZENFS_RRZC_V1_SHADOW \
    -u RRZC_TEST_RECENT_SAMPLE_MS -u RRZC_TEST_VICTIM_PROFILE \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=0 \
    "$program" verify 180 "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
printf '%s\n' "$status" > "$result/runner.exit"
sha256sum "${identity[@]}" > "$result/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
free -b > "$result/memory.final.log"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$result/logs.sha256"
cat "$result/run.stdout.log"
[[ ! -e $result/verify.stdout.log ]] || cat "$result/verify.stdout.log"
exit "$status"
