// 仅新A至F客户端使用；保留原客户端和库，完整核验扫描及同键读改写。
#pragma once
#include "core/db.h"
#include "core/measurements.h"
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <map>
#include <stdexcept>

namespace rrzc_ycsb {
inline uint64_t Hash(const std::string& value) {
  uint64_t out=14695981039346656037ULL;
  for (unsigned char b:value) {out^=b;out*=1099511628211ULL;}
  return out;
}
inline uint64_t ValueHash(const std::vector<ycsbc::DB::Field>& values) {
  if(values.size()!=1 || values.front().name!="field0")
    throw std::runtime_error("仅支持本轮单字段完整请求");
  std::string raw;
  for(const auto& f:values) for(const auto* s:{&f.name,&f.value}) {
    const uint32_t n=s->size();raw.append(reinterpret_cast<const char*>(&n),4);raw+=*s;
  }
  return Hash(raw);
}
inline uint64_t Now() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Audit {
  const char* prefix=std::getenv("YCSB_AUDIT_PREFIX");
  std::map<std::string,uint64_t> expected;
  std::ofstream trace;
  ycsbc::Measurements* measurements=nullptr;
  uint64_t inserts=0,loaded=0,reads=0,updates=0,scans=0,rmws=0,rmw_start=0;
  bool business=false;
  Audit() {
    if(std::getenv("YCSB_AUDIT_IMPORT")) throw std::runtime_error("本轮禁止继承状态或清单");
    if(prefix) {trace.open(std::string(prefix)+".requests",std::ios::binary);
      if(!trace) throw std::runtime_error("请求轨迹无法创建");}
  }
};
inline Audit& State() {static Audit audit;return audit;}
inline void Event(char op,const std::string& key,uint64_t value) {
  auto& a=State();if(!a.prefix)return;
  const uint32_t size=key.size();a.trace.put(op);
  a.trace.write(reinterpret_cast<const char*>(&size),4);a.trace.write(key.data(),size);
  a.trace.write(reinterpret_cast<const char*>(&value),8);
  if(!a.trace)throw std::runtime_error("请求轨迹写入失败");
}
inline void Bind(ycsbc::Measurements* measurements) {State().measurements=measurements;}
inline void MarkTransaction(ycsbc::Operation operation) {
  auto& a=State();if(!a.prefix)return;
  if(!a.business) {a.business=true;a.loaded=a.inserts;Event('P',"",a.loaded);}
  Event('T',"",operation);
}
inline void BeginRmw() {auto& a=State();if(a.prefix)a.rmw_start=Now();}
inline void EndRmw(ycsbc::DB::Status status) {
  const uint64_t end=Now();auto& a=State();if(!a.prefix)return;
  if(!a.rmw_start || !a.measurements || status!=ycsbc::DB::kOK)
    throw std::runtime_error("读改写失败或缺少计时接线");
  a.measurements->Report(ycsbc::READMODIFYWRITE,end-a.rmw_start);a.rmw_start=0;++a.rmws;
}
inline void Record(char operation,const std::string& key,
                   const std::vector<ycsbc::DB::Field>& values,ycsbc::DB::Status status) {
  auto& a=State();if(!a.prefix)return;
  if(status!=ycsbc::DB::kOK)throw std::runtime_error("真实API操作失败");
  const uint64_t hash=ValueHash(values);
  if(operation=='R') {
    if(!a.expected.count(key) || a.expected.at(key)!=hash)
      throw std::runtime_error("读取不符合最新成功写入");
    ++a.reads;
  } else if(operation=='U') {
    if(!a.expected.count(key))throw std::runtime_error("更新未知键");
    a.expected.at(key)=hash;++a.updates;
  } else if(operation=='I') {
    if(!a.expected.emplace(key,hash).second)throw std::runtime_error("重复插入键");
    ++a.inserts;
  } else throw std::runtime_error("未知操作");
  Event(operation,key,operation=='R'?0:hash);
}
inline void RecordScan(const std::string& key,int count,
                       const std::vector<std::vector<ycsbc::DB::Field>>& values,
                       ycsbc::DB::Status status) {
  auto& a=State();if(!a.prefix)return;
  if(status!=ycsbc::DB::kOK || count<=0)throw std::runtime_error("扫描API失败或长度非法");
  auto cursor=a.expected.lower_bound(key);size_t i=0;
  for(;i<static_cast<size_t>(count) && cursor!=a.expected.end();++i,++cursor)
    if(i>=values.size() || ValueHash(values[i])!=cursor->second)
      throw std::runtime_error("扫描缺行、乱序或最新值错误");
  if(i!=values.size())throw std::runtime_error("扫描多行");
  ++a.scans;Event('S',key,count);
}
inline void Save() {
  auto& a=State();if(!a.prefix)return;
  a.trace.flush();
  std::ofstream expected(std::string(a.prefix)+".expected");
  for(const auto& e:a.expected)expected<<e.first<<' '<<e.second<<'\n';
  expected.close();
  std::ofstream summary(std::string(a.prefix)+".counts");
  summary<<"版本=2\n装载="<<a.loaded<<"\n读取="<<a.reads<<"\n更新="<<a.updates
      <<"\n插入="<<a.inserts-a.loaded<<"\n扫描="<<a.scans<<"\n读改写="<<a.rmws
      <<"\n键数="<<a.expected.size()<<'\n';
  summary.close();
  if(!expected || !summary || !a.trace || a.rmw_start)throw std::runtime_error("清单未完整保存");
}
}  // 新A至F请求审计命名空间结束。
