#!/usr/bin/env bash
# 仅本轮新子镜像：确认完整空设备后初始化并做一次真实改选功能联调。
set -euo pipefail
[[ $# == 0 && $(id -u) == 0 ]]
directory=/home/femu/rrzc-v1-execution-smoke-direct-20261014-tests
result=/home/femu/rrzc-v1-execution-results/v1-execution-smoke-direct-20261014
program=$directory/v1_execution_smoke_test
[[ -x $program && $(cat "$directory/build.exit") == 0 && ! -e $result && ! -e $result-aux ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
mkdir -p /home/femu/rrzc-v1-execution-results
mkdir "$result" "$result-aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys; r=json.load(sys.stdin); z=r["zone_list"]; assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 and v["slba"]==i*524288 for i,v in enumerate(z)), "不是32个完整空区，拒绝初始化"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
identity=(plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h plugin/zenfs/fs/zbd_zenfs.cc
  plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc plugin/zenfs/fs/zbdlib_zenfs.h
  plugin/zenfs/fs/rrzc_recent_log.h plugin/zenfs/fs/rrzc_urgency_selector.h
  plugin/zenfs/fs/rrzc_v1_execution.h plugin/zenfs/fs/rrzc_weighted_selector.h
  librocksdb_debug.a plugin/zenfs/util/zenfs "$program" "$directory/v1_execution_smoke_test.cc"
  "$directory/run_v1_execution_smoke_guest.sh")
sha256sum "${identity[@]}" > "$result/identity.initial.sha256"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > "$result/scheduler.log"
status=0
# 明确只操作新客机中已核验全空的模拟NVMe，不格式化OS磁盘或父镜像。
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result-aux" --enable_gc \
  > "$result/mkfs.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/mkfs.exit"
[[ $status == 0 ]]
printf '试验用途,RR阈值,GC启动比例,回收预算字节,紧迫界限秒数,额外有效搬运预算字节,计划真实读次数,填充区数\n功能联调,4096,20,201326592,10,16777216,4032,24\n' > "$result/config.csv"
env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SPACE_WEIGHT_PCT \
  -u ZENFS_RRZC_V1_SHADOW ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
  ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid \
  ZENFS_RRZC_RECLAIM_BYTES=201326592 ZENFS_RRZC_V1_ENABLE=1 \
  "$program" run "$result" > "$result/run.stdout.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/run.exit"
if [[ $status == 0 ]]; then
  env -u ZENFS_ZC_TEST_ENABLE -u ZENFS_ZC_TEST_SOURCE -u ZENFS_RRZC_MODE -u ZENFS_RRZC_SIMPLE_MODE \
    -u ZENFS_RRZC_RECLAIM_BYTES -u ZENFS_RRZC_SPACE_WEIGHT_PCT -u ZENFS_RRZC_V1_ENABLE \
    -u ZENFS_RRZC_V1_SHADOW ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 \
    ZENFS_MIGRATE_RESERVE=1 ZENFS_RR_ENABLE=0 \
    "$program" verify "$result" > "$result/verify.stdout.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
printf '%s\n' "$status" > "$result/runner.exit"
sha256sum "${identity[@]}" > "$result/identity.final.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$result/logs.sha256"
cat "$result/run.stdout.log"
[[ ! -e $result/verify.stdout.log ]] || cat "$result/verify.stdout.log"
exit "$status"
