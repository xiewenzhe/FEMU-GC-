// 合成边界测试，不作为设备运行或维护收益证据。
#include "rrzc_triggered_gc.h"
#include "rrzc_deadline_selector.h"
#include <cassert>
#include <iostream>

static rrzc_deadline::Input Zone(uint64_t id, uint64_t valid, bool hot = false) {
  rrzc_deadline::Input x;
  x.row.candidate.zone = id;
  x.row.candidate.capacity = 128;
  x.row.candidate.valid = valid;
  x.row.safe = true;
  x.row.cost.reclaimed = 128 - valid;
  x.row.cost.credit = hot ? valid : 0;
  x.probability = hot ? rrzc_deadline::kScale : 0;
  return x;
}

int main() {
  const auto observed = rrzc_space_plan::Demand(21, 79, 0);
  assert(observed.known && !observed.bytes);
  const std::vector<rrzc_deadline::Input> pool{Zone(1, 4), Zone(2, 5, true)};
  assert(!rrzc_deadline::Select(pool, observed).found);
  assert(!rrzc_triggered_gc::NeedForSelection(observed, false).bytes);
  const auto selection = rrzc_triggered_gc::NeedForSelection(observed, true);
  assert(selection.bytes == 1 && !observed.bytes);
  assert(selection.free == observed.free && selection.non_free == observed.non_free);
  assert(selection.target == observed.target && selection.waiters == observed.waiters);
  const auto chosen = rrzc_deadline::Select(pool, selection);
  // 已触发时仍由模型在所有安全候选中排序，不强制退回Greedy。
  assert(chosen.found && chosen.zone == 2 && chosen.plans.size() == 2);
  assert(!rrzc_deadline::Select({}, selection).found);
  auto unsafe = pool;
  for (auto& x : unsafe) x.row.safe = false;
  assert(!rrzc_deadline::Select(unsafe, selection).found);
  auto full = Zone(3, 128);
  assert(!rrzc_deadline::Select({full}, selection).found);
  auto positive = rrzc_space_plan::Demand(0, 1100, 0);
  const auto retained = rrzc_triggered_gc::NeedForSelection(positive, true);
  assert(retained.bytes == positive.bytes);
  assert(rrzc_deadline::Select(pool, retained).zone ==
         rrzc_deadline::Select(pool, positive).zone);
  rrzc_space_plan::Need unknown;
  const auto unchanged = rrzc_triggered_gc::NeedForSelection(unknown, true);
  assert(!unchanged.known && !unchanged.bytes);
  std::cout << "已触发零缺口选区、未触发不选区、旧语义、正缺口与安全边界通过\n";
}
