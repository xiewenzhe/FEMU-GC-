#!/usr/bin/env bash
# 同库同负载串行对照；统一入口直接排序，Greedy仅为另一独立实验组。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
target=$(dirname "$(readlink -f "$0")")
build=$(dirname "$target")
result=/home/femu/rrzc-ycsb-results/20261017
[[ $# == 1 && ( $1 == greedy || $1 == direct ) && ! -e $result && $(id -u) == 0 ]]
mode=$1
trap 'code=$?; printf "%s\n" "$code" > "$target/run.exit"; touch "$target/run.finished"' EXIT
[[ $(cat "$build/build.exit") == 0 && $(cat "$target/selftest.exit") == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 && -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-ycsb-results
mkdir "$result" "$result/aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;z=json.load(sys.stdin)["zone_list"];assert len(z)==32 and all(v["state"]=="EMPTY" and v["cap"]==524288 and v["wp"]==v["slba"] for v in z)' < "$result/zones.initial.json"
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
cp "$target/software.sha256" "$result/software.sha256"
cp "$target/workload-locality-wide-zns.properties" "$result/workload.properties"
printf '%s\n' "$mode" > "$result/策略.txt"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
"$build/reset_generation_probe" "$result/probe.initial.log" > "$result/device.initial.json"
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
"$build/reset_generation_probe" "$result/probe.before.log" > "$result/device.before.json"
extra=()
[[ $mode != direct ]] || extra=(ZENFS_RRZC_DIRECT=1)
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/logs.before.txt"
date --iso-8601=ns > "$result/business.start.txt"
status=0
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$result/audit" \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 ZENFS_RRZC_MIGRATION_SPARE=1 \
  ZENFS_RRZC_CONTENT_GUARD=1 \
  "${extra[@]}" python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));os.execv(sys.argv[1],sys.argv[1:])' \
  "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$target/workload-locality-wide-zns.properties" \
  > "$result/ycsb.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/business.exit"
date --iso-8601=ns > "$result/business.end.txt"
python3 - "$result" <<'PY'
from pathlib import Path
import shutil,sys
r=Path(sys.argv[1]);old=set((r/'logs.before.txt').read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1,'唯一新业务日志未确认'
shutil.copyfile(logs[0],r/'zenfs.run.log')
PY
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=0 ZENFS_RRZC_CONTENT_GUARD=1 \
    "$directory/verify_ycsb" zns "$result" "$result/audit.expected" > "$result/verify.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
"$build/reset_generation_probe" "$result/probe.after.log" > "$result/device.after.json"
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$target/logs.sha256.tmp"
mv "$target/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
