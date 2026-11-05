#!/usr/bin/env bash
# 窗口模型与Greedy同库不限速对照；只操作全新模拟盘，不改变RR/GC及安全复核。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 3 && $(id -u) == 0 && ( $2 == greedy || $2 == window ) ]]
tag=$1
mode=$2
ops=$3
[[ $tag == smoke-$mode || $tag == short-$mode || $tag == long-$mode ]]
case $tag in
  smoke-*) [[ $ops == 8192 ]]; records=8192; hot=64; count=2048; rest=10;;
  short-*) [[ $ops == 400000 ]]; records=491520; hot=4096; count=65536; rest=30;;
  long-*) [[ $ops == 4000000 ]]; records=491520; hot=4096; count=65536; rest=30;;
esac
result=/home/femu/rrzc-sst-results/$tag
[[ ! -e $result && $(cat "$target/window.build.exit") == 0 && $(cat "$target/selftest.exit") == 0 &&
   $(blockdev --getsize64 /dev/nvme0n1) == 4294967296 &&
   -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-sst-results
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
cp "$target/software.sha256" "$result/software.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]*512==134217728 for v in z)' < "$result/zones.initial.json"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
"$target/sst_device_probe" "$result/probe.initial.log" > "$result/device.initial.json"
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
"$target/sst_device_probe" "$result/probe.before.log" > "$result/device.before.json"
cp "$target/workload-stable-unlimited.properties" "$result/workload.properties"
sed -i -e "s/^recordcount=.*/recordcount=$records/" -e "s/^operationcount=.*/operationcount=$ops/" \
  -e "s/^rrzc.read_key_count=.*/rrzc.read_key_count=$hot/" -e "s/^sleepafterload=.*/sleepafterload=$rest/" "$result/workload.properties"
printf '\nrrzc.update_key_start=0\nrrzc.update_key_count=%s\n' "$count" >> "$result/workload.properties"
greedy=0
window=0
if [[ $mode == greedy ]]; then greedy=1; else window=1; fi
printf '策略=%s\nGreedy开关=%s\n窗口收益开关=%s\n稳定收益开关=0\n' "$mode" "$greedy" "$window" > "$result/策略.txt"
# 双方统一沿用已通过验收的RR队列，不改变阈值、周期和迁移主体。
printf '等待优先开关=1\n' > "$result/RR调度.txt"
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/logs.before.txt"
date --iso-8601=ns > "$result/business.start.txt"
status=0
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  YCSB_RANDOM_SEED=20261017 YCSB_PHASE_TIMING=1 YCSB_AUDIT_PREFIX="$result/audit" \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_RRZC_DIRECT=1 \
  ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_ETA_OBSERVE=1 \
  ZENFS_RRZC_PAIR_GREEDY="$greedy" ZENFS_RRZC_WINDOW_CREDIT="$window" ZENFS_RRZC_STABLE_CREDIT=0 ZENFS_RR_FAIR_QUEUE=1 \
  python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));os.execv(sys.argv[1],sys.argv[1:])' \
  "$target/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$result/workload.properties" \
  > "$result/ycsb.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/business.exit"
date --iso-8601=ns > "$result/business.end.txt"
python3 - "$result" <<'PY'
from pathlib import Path
import shutil,sys
r=Path(sys.argv[1]);old=set((r/'logs.before.txt').read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'唯一业务维护日志未确认'
shutil.copyfile(logs[0],r/'zenfs.run.log')
PY
"$target/sst_device_probe" "$result/probe.after.log" > "$result/device.after.json"
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=0 ZENFS_RRZC_CONTENT_GUARD=1 \
    "$target/verify_ycsb" zns "$result" "$result/audit.expected" > "$result/verify.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$target/logs.sha256.tmp"
mv "$target/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
