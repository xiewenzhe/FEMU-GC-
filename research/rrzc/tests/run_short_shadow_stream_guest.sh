#!/usr/bin/env bash
# 独立短期影子包装：不修改原请求计划、旧运行脚本或真实Greedy选择。
set -euo pipefail
[[ $# == 1 && $(id -u) == 0 ]]
profile=$1
case "$profile" in
  original)
    label=v1-growth-stream-greedy-20261014
    script=run_v1_growth_stream_guest.sh
    args=(greedy)
    ;;
  maintenance-cases)
    label=short-shadow-cases-20261014
    script=run_short_shadow_cases_guest.sh
    args=()
    ;;
  *) echo '不是本轮固定负载配置，拒绝执行。' >&2; exit 2 ;;
esac
directory=/home/femu/rrzc-$label-tests
result=/home/femu/rrzc-stream-results/$label
[[ -f $directory/$script && ! -e $result && ! -e $result-aux ]]
[[ $(df -B1 --output=avail /home/femu | tail -1 | tr -d ' ') -ge 2147483648 ]]
unset ZENFS_RRZC_V1_ENABLE ZENFS_RRZC_V1_SHADOW
export ZENFS_RRZC_V1_GROWTH=1 ZENFS_RRZC_SHORT_SHADOW=1
status=0
# 限制普通日志为64MiB；原脚本仍自行核对设备全空后才初始化模拟NVMe。
python3 -c 'import os,resource,sys; resource.setrlimit(resource.RLIMIT_FSIZE,(64<<20,64<<20)); os.execv("/bin/bash",["bash"]+sys.argv[1:])' \
  "$directory/$script" "${args[@]}" || status=$?
if [[ -d $result ]]; then
  printf '负载配置,实际策略,短期影子,增长采样,真实入口,界限秒数,额外搬运预算字节\n%s,Greedy,1,1,0,10,16777216\n' "$profile" \
    > "$result/short-shadow-config.csv"
  printf '%s\n' "$status" > "$result/short-shadow-wrapper.exit"
  sha256sum "$directory/run_short_shadow_stream_guest.sh" \
    /home/femu/rocksdb/plugin/zenfs/fs/rrzc_short_shadow.h > "$result/short-shadow-identity.sha256"
  # 原脚本的清单保留；新清单同时覆盖原清单和额外影子身份记录。
  find "$result" -type f ! -name short-shadow.logs.sha256 -print0 | sort -z | xargs -0 sha256sum \
    > "$result/short-shadow.logs.sha256"
fi
exit "$status"
