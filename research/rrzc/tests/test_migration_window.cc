// 合成内存测试，不访问块设备，不将计数当作真实实验收益。
#include "rrzc_migration_window.h"
#include <cassert>
#include <iostream>
#include <thread>

int main() {
  using rrzc_observe::MigrationWindow;
  using rrzc_observe::MigrationWindowEnabled;
  assert(!MigrationWindowEnabled(nullptr));
  assert(!MigrationWindowEnabled("0"));
  assert(!MigrationWindowEnabled("true"));
  assert(MigrationWindowEnabled("1"));
  uint64_t now = 100;
  MigrationWindow disabled(false, [&] { assert(false); return now; });
  assert(!disabled.Mark({1}));
  assert(!MigrationWindow::Check(disabled.Freeze(), disabled.Freeze(), 1, 100, 110).known);
  MigrationWindow tracker(true, [&] { return now; });
  const auto first = tracker.Freeze();
  now = 110; auto last = tracker.Freeze();
  assert(MigrationWindow::Check(first, last, 1, 100, 110).clean);
  assert(!MigrationWindow::Check(first, last, 1, 99, 110).known);
  assert(!MigrationWindow::Check(first, last, 1, 100, 111).known);
  now = 111;
  auto source = tracker.Mark({1});
  auto target = tracker.Mark({2});
  now = 120; auto active = tracker.Freeze();
  assert(!MigrationWindow::Check(first, active, 1, 100, 120).clean);
  assert(!MigrationWindow::Check(first, active, 2, 100, 120).clean);
  assert(MigrationWindow::Check(first, active, 3, 100, 120).clean);
  target.reset(); source.reset();
  auto ended = tracker.Freeze();
  assert(ended.Get(1).sequence == 2 && ended.Get(1).active == 0);
  assert(!MigrationWindow::Check(first, ended, 1, 100, 120).clean);
  // 窗口从完成边界之后开始，较早迁移不永久污染该Zone。
  now = 121; const auto clean_begin = tracker.Freeze();
  now = 140; const auto clean_end = tracker.Freeze();
  assert(MigrationWindow::Check(clean_begin, clean_end, 1, 121, 140).clean);
  assert(!MigrationWindow::Check(ended, clean_end, 1, 120, 140).clean);
  // 后续活动不能修改已冻结的过去证据；源目标和重叠任务分别计数。
  now = 141; source = tracker.Mark({1,2}); target = tracker.Mark({2});
  assert(MigrationWindow::Check(clean_begin, clean_end, 1, 121, 140).clean);
  assert(tracker.Freeze().Get(2).active == 2);
  source.reset(); assert(tracker.Freeze().Get(2).active == 1);
  target.reset(); assert(tracker.Freeze().Get(2).active == 0);
  // 失败／抛异常也必须结束标记，但不得把失败标记删除成无迁移。
  try { auto failed = tracker.Mark({4}); throw 1; } catch (int) {}
  assert(tracker.Freeze().Get(4).active == 0 && tracker.Freeze().Get(4).sequence == 2);
  MigrationWindow other(true, [&] { return now; });
  assert(!MigrationWindow::Check(first, other.Freeze(), 1, 100, 141).known);
  now = 130;
  assert(!tracker.Freeze().reliable);
  now = 150; assert(!tracker.Freeze().reliable);
  // 并发发布使用真实单调时钟；没有持统计锁等待业务的路径。
  MigrationWindow concurrent(true);
  const auto before = concurrent.Freeze();
  std::vector<std::thread> workers;
  for (unsigned i=0;i<4;++i) workers.emplace_back([&] {
    for (unsigned j=0;j<1000;++j) { auto guard=concurrent.Mark({1,2}); }
  });
  for (auto& worker:workers) worker.join();
  const auto after = concurrent.Freeze();
  assert(after.reliable && after.Get(1).active == 0 && after.Get(1).sequence == 8000);
  assert(!MigrationWindow::Check(before, after, 1, before.captured_ns, after.captured_ns).clean);
  std::cout << "迁移窗口：默认关闭、源目标、活动、提交范围、窗口边界、冻结、实例隔离、时钟和并发检查通过。\n";
}
