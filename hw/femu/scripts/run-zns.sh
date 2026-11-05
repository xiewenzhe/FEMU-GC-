#!/usr/bin/env bash
# 原启动脚本作者：Huaicheng Li <huaicheng@vt.edu>。
# 手动启动入口，SSD参数读取research/rrzc/configs/finerr-zns.json。
# --dry-run仅显示命令；--check仅检查资源，均不启动或修改镜像。
# 主机环境不会进入客机；ZenFS库及业务环境需在客机单独部署。
set -euo pipefail
script_dir=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
if [[ -f "$script_dir/../../../research/rrzc/manual_zns.py" ]]; then
    project_dir=$(cd -- "$script_dir/../../.." && pwd)
elif [[ -f "$script_dir/../research/rrzc/manual_zns.py" ]]; then
    # 兼容构建时直接复制源脚本到build目录的情况。
    project_dir=$(cd -- "$script_dir/.." && pwd)
else
    echo '无法定位项目配置与启动模块' >&2
    exit 1
fi
exec python3 "$project_dir/research/rrzc/manual_zns.py" "$@"
