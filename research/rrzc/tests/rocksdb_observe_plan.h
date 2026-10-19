// 独立测试负载的固定计划；旧入口默认行为保持不变。
#pragma once
#include <cstring>
#include <cstdint>
// 固定逻辑条带锚点，不读取设备风险或未来失效来决定请求，不保证物理同区。
namespace rrzc_anchor {
constexpr unsigned Stripes=4,StripeKeys=65536,AnchorOffset=32768,AnchorKeys=8192;
constexpr unsigned WritablePerStripe=StripeKeys-AnchorKeys;
constexpr unsigned WritableKeys=Stripes*WritablePerStripe;
constexpr unsigned NoOrdinal=UINT32_MAX,AnchorReadStart=45*400;
inline unsigned WriteKey(unsigned ordinal) {
  ordinal%=WritableKeys;
  unsigned stripe=ordinal/WritablePerStripe,offset=ordinal%WritablePerStripe;
  return stripe*StripeKeys+offset+(offset>=AnchorOffset?AnchorKeys:0);
}
inline unsigned Ordinal(unsigned key) {
  if(key>=Stripes*StripeKeys) return NoOrdinal;
  unsigned stripe=key/StripeKeys,offset=key%StripeKeys;
  if(offset>=AnchorOffset&&offset<AnchorOffset+AnchorKeys) return NoOrdinal;
  return stripe*WritablePerStripe+offset-(offset>=AnchorOffset+AnchorKeys?AnchorKeys:0);
}
inline unsigned ReadKey(unsigned request) {
  if(request<AnchorReadStart||request%2==0) return ((request/2)%16)*1024;
  return ((request/2)%Stripes)*StripeKeys+AnchorOffset+AnchorKeys/2;
}
}  // 固定锚点负载命名空间。
namespace rrzc_real {
constexpr unsigned BaseKeys=524288,PressureKeys=655360,ColdKey=524287;
struct Plan {unsigned keys=BaseKeys,write_seconds=120,write_rate=8192;bool anchors=false;const char* label="rocksdb-observe-20261016";};
inline bool Parse(const char* profile,Plan* plan) {
  *plan=Plan{};
  if(!profile||std::strcmp(profile,"original")==0) return true;
  // 仅减半固定写速率，总写量和原条带／读请求不变；不修改生产机制。
  if(std::strcmp(profile,"anchors180slow")==0) {
    plan->keys=PressureKeys;plan->write_seconds=180;plan->write_rate=4096;plan->anchors=true;
    plan->label="rocksdb-anchor-slow-20261016";return true;
  }
  if(std::strcmp(profile,"anchors90")==0) {
    plan->keys=PressureKeys;plan->write_seconds=90;plan->anchors=true;
    plan->label="rocksdb-anchor-20261016";return true;
  }
  if(std::strcmp(profile,"pair90")==0) {
    plan->keys=PressureKeys;plan->write_seconds=90;
    plan->label="rocksdb-pair-rate-20261016";return true;
  }
  if(std::strcmp(profile,"recent90")==0) {
    plan->keys=PressureKeys;plan->write_seconds=90;
    plan->label="rocksdb-recent-20261016";return true;
  }
  if(std::strcmp(profile,"pressure512")!=0) return false;
  plan->keys=PressureKeys;plan->label="rocksdb-pressure-20261016";return true;
}
}  // 测试计划命名空间。
