// 公共默认关闭的内容隔离：分类不预测热度、不改变受害区评分。
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
namespace rrzc_content {
constexpr uint8_t kUnknown=0,kSst=1,kOther=2,kMixed=3;
inline bool Enabled(const char* value) {return value && !std::strcmp(value,"1");}
inline uint8_t Classify(const std::string& name) {
  return name.size()>=4 && name.compare(name.size()-4,4,".sst")==0 ? kSst : kOther;
}
inline bool Compatible(uint8_t observed,uint8_t requested) {
  return (requested==kSst || requested==kOther) && observed==requested;
}
inline bool Claimable(uint8_t observed,uint8_t requested) {
  return (requested==kSst || requested==kOther) && (!observed || observed==requested);
}
}  // 分类只在真实Reset后清零，挂载从实际文件映射重建。
