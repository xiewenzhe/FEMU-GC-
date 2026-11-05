#!/usr/bin/env bash
# 只在新子镜像复制客户端、补齐A至F观测并重链接；不修改库或旧客户端。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
old=/home/femu/rrzc-ycsb-retention-20261017-tests
[[ $# == 0 && $(id -un) == femu && ! -e $target/ycsb ]]
trap 'code=$?; printf "%s\n" "$code" > "$target/build.exit"; touch "$target/build.finished"' EXIT
cd /home/femu/rocksdb
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.log"
cp -a "$old/ycsb" "$target/ycsb"
cp "$target/rrzc_ycsb_suite.h" "$target/ycsb/rrzc_ycsb_audit.h"
cp "$target/rrzc_client_phase.h" "$target/ycsb/core/"
cd "$target/ycsb"
git apply --unidiff-zero --check "$target/ycsb-phase-timing.patch"
git apply --unidiff-zero "$target/ycsb-phase-timing.patch"
git apply --unidiff-zero --reverse --check "$target/ycsb-phase-timing.patch"
git apply --unidiff-zero --check "$target/ycsb-suite-observation.patch"
git apply --unidiff-zero "$target/ycsb-suite-observation.patch"
git apply --unidiff-zero --reverse --check "$target/ycsb-suite-observation.patch"
make --trace -W core/ycsbc.cc -W core/core_workload.cc -W core/db_wrapper.h -j2 BIND_ROCKSDB=1 \
  EXTRA_CXXFLAGS="-std=c++20 -fno-rtti -I/home/femu/rocksdb/include" \
  LDFLAGS="-Wl,-u,zenfs_filesystem_reg /home/femu/rocksdb/librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -lpthread -ldl -lrt" \
  > "$target/ycsb-build.log" 2>&1
cd /home/femu/rocksdb
for pair in suite_verify_ycsb:verify_ycsb suite_device_probe:sst_device_probe; do
  g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
    "$target/${pair%:*}.cc" librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
    -o "$target/${pair#*:}" > "$target/${pair#*:}-build.log" 2>&1
done
mkdir "$target/原始负载"
for letter in a b c d e f; do
  cp "$target/ycsb/workloads/workload$letter" "$target/原始负载/workload$letter"
  for repeat in first second; do
    out=$target/selftest-$letter-$repeat
    mkdir "$out"
    python3 "$target/ycsb_af.py" "$target/workload-stable-unlimited.properties" \
      "$target/原始负载/workload$letter" "$letter" 1024 8192 --posix "$out/db" > "$out/workload.properties"
    env -i PATH=/usr/bin:/bin LANG=C.UTF-8 YCSB_RANDOM_SEED=20261023 YCSB_PHASE_TIMING=1 \
      YCSB_AUDIT_PREFIX="$out/audit" "$target/ycsb/ycsb" -load -run -db rocksdb -threads 1 \
      -P "$out/workload.properties" > "$out/ycsb.log" 2>&1
    "$target/verify_ycsb" posix "$out/db" "$out/audit.expected" > "$out/verify.log" 2>&1
  done
done
python3 - "$target" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ycsb_af import check_trace,latencies,digest
t=Path(sys.argv[1]);results={}
for letter in 'abcdef':
    rows=[]
    for repeat in ('first','second'):
        p=t/f'selftest-{letter}-{repeat}'
        trace=check_trace(p/'audit.requests',1024,8192,letter)
        latency=latencies((p/'ycsb.log').read_text(),trace)
        assert f'键数={trace["最终键数"]}' in (p/'verify.log').read_text()
        rows.append(dict(轨迹=trace,延迟=latency,请求=digest(p/'audit.requests'),最终值=digest(p/'audit.expected')))
    assert rows[0]['请求']==rows[1]['请求'] and rows[0]['最终值']==rows[1]['最终值']
    results[letter]=rows
(t/'普通目录验收.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
PY
sha256sum -c "$target/parent.software.sha256" > "$target/software.parent.after.log"
cp "$target/parent.software.sha256" "$target/software.sha256"
sha256sum "$target/ycsb/ycsb" "$target/verify_ycsb" "$target/sst_device_probe" \
  "$target/ycsb/core/db_wrapper.h" "$target/ycsb/core/core_workload.cc" \
  "$target/ycsb/rrzc_ycsb_audit.h" "$target/run_ycsb_af_guest.sh" "$target/ycsb_af.py" \
  "$target/strict_stats_guest.py" "$target/原始负载/"* >> "$target/software.sha256"
tar --exclude='*.o' -czf "$target/client-source.tar.gz" -C "$target/ycsb" \
  Makefile core rocksdb rrzc_ycsb_audit.h workloads
printf '0\n' > "$target/selftest.exit"
