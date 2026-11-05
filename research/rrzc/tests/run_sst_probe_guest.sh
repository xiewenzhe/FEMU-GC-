#!/usr/bin/env bash
# 每臂仅格式化已验证全空的隔离模拟盘；保留日志、真实业务及独立全键校验。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ ( $# == 1 || $# == 2 ) && ( $1 == smoke || $1 == disjoint || $1 == overlap ) && $(id -u) == 0 ]]
mode=$1
records=${2:-327680}
[[ $records == 327680 || $records == 491520 ]]
result=/home/femu/rrzc-sst-results/$mode
[[ ! -e $result && $(cat "$target/build.exit") == 0 && $(cat "$target/selftest.exit") == 0 &&
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
cp "$target/workload-sst-probe.properties" "$result/workload.properties"
# 只改本轮新派生配置，旧公共模板及历史规模默认值不变。
sed -i "s/^recordcount=.*/recordcount=$records/" "$result/workload.properties"
start=4096
[[ $mode != overlap ]] || start=0
printf '\nrrzc.update_key_start=%s\nrrzc.update_key_count=65536\n' "$start" >> "$result/workload.properties"
if [[ $mode == smoke ]]; then
  # 只改新派生配置，不覆盖公共模板；小ZNS测试优先检查真实库和数据正确性。
  sed -i -e 's/^recordcount=.*/recordcount=8192/' -e 's/^operationcount=.*/operationcount=8192/' \
    -e 's/^rrzc.read_key_count=.*/rrzc.read_key_count=64/' \
    -e 's/^rrzc.update_key_start=.*/rrzc.update_key_start=64/' \
    -e 's/^rrzc.update_key_count=.*/rrzc.update_key_count=2048/' \
    -e 's/^sleepafterload=.*/sleepafterload=10/' "$result/workload.properties"
fi
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/logs.before.txt"
date --iso-8601=ns > "$result/business.start.txt"
status=0
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$result/audit" \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 \
  ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
  ZENFS_RRZC_DIRECT=1 ZENFS_RRZC_RECENT_ETA=1 ZENFS_RRZC_ETA_OBSERVE=1 \
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
