#!/usr/bin/env bash
# 同一次新设备启动内先均匀更新预热、再latest热读；两阶段独立记账。
set -euo pipefail
directory=/home/femu/rrzc-ycsb-retention-20261017-tests
# 新反压实验复用同一业务路径；不覆盖旧实验目录及其冻结脚本。
target=${RRZC_RUN_TARGET:-$directory/overlap-20261018}
[[ $target == "$directory/overlap-20261018" || $target == "$directory/backpressure-20261018" ]]
result=/home/femu/rrzc-ycsb-results/20261017
trap 'code=$?; printf "%s\n" "$code" > "$target/run.exit"; touch "$target/run.finished"' EXIT
[[ $# == 1 && $1 == greedy && ! -e $result && $(id -u) == 0 ]]
[[ $(cat "$target/build.exit") == 0 && $(cat "$target/selftest.exit") == 0 ]]
[[ $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
[[ -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir -p /home/femu/rrzc-ycsb-results
mkdir "$result" "$result/aux" "$result/warmup"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.initial.json"
python3 -c 'import json,sys;r=json.load(sys.stdin);z=r["zone_list"];assert r["nr_zones"]==32 and len(z)==32 and all(v["state"]=="EMPTY" and v["wp"]==v["slba"] and v["cap"]==524288 for v in z)' < "$result/zones.initial.json"
cd /home/femu/rocksdb
sha256sum -c "$target/software.sha256" > "$result/software.before.log"
cp "$target/software.sha256" "$result/software.sha256"
printf 'greedy\n' > "$result/策略.txt"
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" --enable_gc > "$result/mkfs.log" 2>&1
status=0
for stage in warmup evaluate; do
  output=$result/warmup
  properties=$target/workload-pressure-warmup-zns.properties
  flags=(-load -run)
  inheritance=()
  if [[ $stage == evaluate ]]; then
    output=$result
    properties=$target/workload-read-latest-zns.properties
    flags=(-run)
    cp "$result/warmup/audit.expected" "$result/audit.inherited.expected"
    inheritance=(YCSB_AUDIT_IMPORT="$result/audit.inherited.expected" YCSB_AUDIT_KEYS=655360)
    # 日志名按秒创建，避开前一进程关闭时间；不在阶段间做全键读取。
    sleep 2
  fi
  printf '%s\n' "$stage" > "$result/阶段.txt"
  cp "$properties" "$output/workload.properties"
  find /tmp -maxdepth 1 -type f -name 'zenfs_nvme0n1_*.log' -print > "$output/logs.before.txt"
  date --iso-8601=ns > "$output/business.start.txt"
  env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
    YCSB_RANDOM_SEED=20261017 YCSB_AUDIT_PREFIX="$output/audit" "${inheritance[@]}" \
    ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
    ZENFS_RR_ENABLE=1 ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=201326592 \
    ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 ZENFS_RRZC_RECENT_SAMPLER=1 \
    ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe ZENFS_RRZC_RESERVE_TARGET=1 \
    ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RRZC_RESERVE_EXCHANGE=0 ZENFS_RRZC_EMPTY_RESERVE=1 \
    ZENFS_RRZC_EXACT_LIVE=1 ZENFS_RRZC_SPACE_EMERGENCY=1 ZENFS_RRZC_ALIGN_RECENT=1 \
    python3 -c 'import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(256<<20,256<<20));os.execv(sys.argv[1],sys.argv[1:])' \
    "$directory/ycsb/ycsb" "${flags[@]}" -db rocksdb -threads 1 -P "$properties" \
    > "$output/ycsb.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$output/business.exit"
  date --iso-8601=ns > "$output/business.end.txt"
  python3 - "$output" <<'PY'
from pathlib import Path
import shutil,sys
root=Path(sys.argv[1]);old=set((root/'logs.before.txt').read_text().splitlines())
logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
assert len(logs)==1, '本阶段新增维护日志不唯一，拒绝猜测'
shutil.copyfile(logs[0],root/'zenfs.run.log')
PY
  [[ $status == 0 ]] || break
done
if [[ $status == 0 ]]; then
  printf 'verify\n' > "$result/阶段.txt"
  env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_RR_ENABLE=0 \
    "$directory/verify_ycsb" zns "$result" "$result/audit.expected" > "$result/verify.log" 2>&1 || status=$?
  printf '%s\n' "$status" > "$result/verify.exit"
fi
sha256sum -c "$target/software.sha256" > "$result/software.after.log"
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
printf '%s\n' "$status" > "$result/runner.exit"
find "$result" -type f ! -name logs.sha256 -print0 | sort -z | xargs -0 sha256sum > "$target/logs.sha256.tmp"
mv "$target/logs.sha256.tmp" "$result/logs.sha256"
exit "$status"
