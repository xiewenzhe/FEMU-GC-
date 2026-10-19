#!/usr/bin/env bash
# 只运行内存测试；不访问NVMe，不分配或重置Zone。
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash research/rrzc/tests/run_short_shadow_unit_tests.sh
cost_test_dir=$(mktemp -d /tmp/rrzc-cost-observe-tests.XXXXXX)
printf '成本观察构建目录：%s\n' "$cost_test_dir"
awk '/^\+std::vector<rrzc_cost::ValidityPoint> ZonedBlockDevice::GetCostValidityReadings\(/ || /^\+rrzc_cost::ReserveObservation ZonedBlockDevice::GetMigrationReserveObservation\(/ { on=1 }
     on { print substr($0,2) } on && /^\+}$/ { on=0 }' research/rrzc/zenfs-cost-observe.patch \
  > "$cost_test_dir/cost-backend-under-test.inc"
test -s "$cost_test_dir/cost-backend-under-test.inc"
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  research/rrzc/tests/test_rrzc_cost_observe.cc -o "$cost_test_dir/core"
g++ -std=c++17 -Wall -Wextra -Werror -pthread -fsanitize=address,undefined -I "$cost_test_dir" \
  research/rrzc/tests/test_cost_backend.cc -o "$cost_test_dir/backend"
ASAN_OPTIONS=detect_leaks=0 "$cost_test_dir/core"
ASAN_OPTIONS=detect_leaks=0 "$cost_test_dir/backend"
