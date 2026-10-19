// 仅测试计划：三个独立满区，成本与读速率预先固定，不读取维护结果。
#pragma once
#include <cstdint>
namespace rrzc_workload {
constexpr unsigned AnchorMiB(unsigned /*代际*/,unsigned kind) { return 16+8*kind; }
constexpr unsigned VersionMiB(unsigned generation,unsigned kind) { return 256-AnchorMiB(generation,kind); }
constexpr const char* Kind(unsigned kind) { return kind==0?"cold":kind==1?"warm":"hot"; }
constexpr unsigned PostRate(unsigned kind) { return kind==1?28:32; }
constexpr uint64_t PaceNs=39062500ULL;
}  // 命名空间结束。
