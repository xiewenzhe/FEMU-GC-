#!/usr/bin/env bash
# 仅限定更新键范围；实际Greedy及全部设备、GC和模型条件沿用已完成配对。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
result=/home/femu/rrzc-ycsb-results/20261017
nowal=$directory/locality-20261019
[[ $# == 1 && $1 == greedy && ! -e $result ]]
mode=$1
properties=$nowal/workload-locality-zns.properties
trap 'code=$?; printf "%s\n" "$code" > "$nowal/run.exit"; touch "$nowal/run.finished"' EXIT
[[ $(id -u) == 0 && $(cat "$nowal/build.exit") == 0 && $(cat "$nowal/selftest.exit") == 0 ]]
grep -Fxq 'rocksdb.disable_wal=true' "$properties"
grep -Fxq 'readproportion=0.5' "$properties"
grep -Fxq 'updateproportion=0.5' "$properties"
grep -Fxq 'limit.ops=1000' "$properties"
grep -Fxq 'operationcount=1200000' "$properties"
grep -Fxq 'requestdistribution=uniform' "$properties"
grep -Fxq 'rrzc.independent_keys=true' "$properties"
grep -Fxq 'rrzc.read_key_count=4096' "$properties"
grep -Fxq 'rrzc.update_key_start=4096' "$properties"
grep -Fxq 'rrzc.update_key_count=61440' "$properties"
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-ycsb-results
mkdir "$result" "$result/aux"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;r=json.load(sys.stdin);z=r["zone_list"];assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 for v in z), "不是本轮新全空设备"' < "$result/zones.initial.json"
cd /home/femu/rocksdb
sha256sum -c "$nowal/software.sha256" > "$result/software.before.log"
cp "$nowal/software.sha256" "$result/software.sha256"
cp "$properties" "$result/workload.properties"
printf '%s\n' "$mode" > "$result/策略.txt"
# 前置核验通过后，仅初始化这个新虚拟设备；不变更GC原20%启动条件。
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
guarded=execute
rank=0
retention=observe
[[ $mode != greedy ]] || guarded=shadow
[[ $mode != cost ]] || rank=1
[[ $mode != retention ]] || retention=execute
find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$result/logs.before.txt"
date --iso-8601=ns > "$result/business.start.txt"
status=0
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$result/audit" \
  ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
  ZENFS_RRZC_GUARDED_MODE="$guarded" ZENFS_RRZC_COST_RANK="$rank" ZENFS_RRZC_RECENT_SAMPLER=1 \
  ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE="$retention" ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
  ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 ZENFS_RRZC_MIGRATION_SPARE=1 \
  python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));os.execv(sys.argv[1],sys.argv[1:])' \
  "$directory/ycsb/ycsb" -load -run -db rocksdb -threads 1 -P "$properties" \
  > "$result/ycsb.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$result/business.exit"
date --iso-8601=ns > "$result/business.end.txt"
# 先冻结完整业务日志，再单独只读校验；不把校验读取算作业务RR或延迟。
python3 - "$result" <<'PY'
from pathlib import Path
import shutil,sys
root=Path(sys.argv[1]);old=set((root/'logs.before.txt').read_text().splitlines())
logs=sorted(p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old)
assert len(logs)==1, '业务日志不是唯一新文件，拒绝猜测'
shutil.copyfile(logs[0],root/'zenfs.run.log')
PY
if [[ $status == 0 ]]; then
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=0 \
    "$directory/verify_ycsb" zns "$result" "$result/audit.expected" > "$result/verify.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum -c "$nowal/software.sha256" > "$result/software.after.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$nowal/logs.sha256.tmp"
mv "$nowal/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
