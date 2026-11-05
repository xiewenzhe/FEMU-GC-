#!/usr/bin/env bash
# 同一已验收业务与模型；只修复日志预算和结束日志的重复复制，不改请求。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $# == 6 && $(id -u) == 0 ]]
tag=$1; mode=$2; letter=$3; records=$4; ops=$5; seed=$6
[[ $mode == space || $mode == model ]]
[[ $letter =~ ^[a-f]$ && $tag =~ ^(smoke|full)-[a-f]-(space|model)$ ]]
[[ $tag == smoke-$letter-$mode || $tag == full-$letter-$mode ]]
[[ $records =~ ^[0-9]+$ && $ops =~ ^[0-9]+$ && $seed =~ ^[0-9]+$ ]]
result=/home/femu/rrzc-sst-results/$tag
[[ ! -e $result && $(cat "$target/build.exit") == 0 && $(cat "$target/selftest.exit") == 0 &&
   $(blockdev --getsize64 /dev/nvme0n1) == 34359738368 &&
   -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-sst-results
mkdir "$result" "$result/aux"
trap 'code=$?; printf "%s\n" "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/stream.software.sha256" > "$result/software.before.log"
cp "$target/stream.software.sha256" "$result/software.sha256"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==256 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]*512==134217728 for v in z)' < "$result/zones.initial.json"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
"$target/sst_device_probe" "$result/probe.initial.log" > "$result/device.initial.json"
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
"$target/sst_device_probe" "$result/probe.before.log" > "$result/device.before.json"
python3 "$target/ycsb_af.py" "$target/workload-stable-unlimited.properties" \
  "$target/原始负载/workload$letter" "$letter" "$records" "$ops" > "$result/workload.properties"
enabled=0; greedy=1
if [[ $mode == model ]]; then enabled=1; greedy=0; fi
printf '版本=1\n策略=%s\nRR及留存评分=%s\n边界开关=%s\n候选范围=expanded\nGC启动百分比=20\nRR阈值=1024\n' \
  "$mode" "$enabled" "$enabled" > "$result/策略.txt"
printf '单文件上限字节=4294967296\n结束日志保存=同盘硬链接\n' > "$result/日志预算.txt"
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/logs.before.txt"
date --iso-8601=ns > "$result/business.start.txt"
status=0
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  YCSB_RANDOM_SEED="$seed" YCSB_PHASE_TIMING=1 YCSB_AUDIT_PREFIX="$result/audit" \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_RRZC_DIRECT=1 \
  ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_ETA_OBSERVE=1 ZENFS_RR_FAIR_QUEUE=1 \
  ZENFS_RRZC_PAIR_GREEDY="$greedy" ZENFS_RRZC_WINDOW_CREDIT="$enabled" ZENFS_RRZC_STABLE_CREDIT=0 \
  ZENFS_RRZC_PERSISTENT_CREDIT="$enabled" ZENFS_RRZC_EXPECTED_RR="$enabled" \
  ZENFS_RRZC_CANDIDATE_POOL=expanded ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 \
  ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL="$enabled" \
  python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(4<<30,4<<30));os.execv(sys.argv[1],sys.argv[1:])' \
  "$target/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$result/workload.properties" \
  > "$result/ycsb.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/business.exit"
date --iso-8601=ns > "$result/business.end.txt"
python3 - "$result" <<'PY'
from pathlib import Path
import os,sys
r=Path(sys.argv[1]);old=set((r/'logs.before.txt').read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'无法唯一定位业务维护日志'
assert logs[0].stat().st_dev==r.stat().st_dev,'日志不在同一文件系统，不盲目重复复制'
os.link(logs[0],r/'zenfs.run.log')
PY
"$target/sst_device_probe" "$result/probe.after.log" > "$result/device.after.json"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
python3 "$target/strict_stats_guest.py" > "$result/strict.business.json"
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=1 ZENFS_RR_STRICT=1 ZENFS_RR_STRICT_AUDIT=1 \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
    "$target/verify_ycsb" zns "$result" "$result/audit.expected" > "$result/verify.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum -c "$target/stream.software.sha256" > "$result/software.after.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.after-verify.json"
python3 "$target/strict_stats_guest.py" > "$result/strict.after-verify.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$target/logs.sha256.tmp"
mv "$target/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
