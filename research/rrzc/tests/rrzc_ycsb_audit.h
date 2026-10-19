// 仅单线程、单字段完整读写实验使用；记录发生在YCSB调用计时结束之后。
#pragma once
#include "core/db.h"
#include <charconv>
#include <cstdlib>
#include <fstream>
#include <map>
#include <stdexcept>

namespace rrzc_ycsb {
inline uint64_t Hash(const std::string& value) {
  uint64_t out=14695981039346656037ULL;
  for (unsigned char b:value) { out^=b; out*=1099511628211ULL; }
  return out;
}
inline uint64_t ValueHash(const std::vector<ycsbc::DB::Field>& values) {
  std::string raw;
  for (const auto& f:values) for (const auto* s:{&f.name,&f.value}) {
    const uint32_t n=s->size(); raw.append(reinterpret_cast<const char*>(&n),4); raw+=*s;
  }
  return Hash(raw);
}
struct Audit {
  const char* prefix=std::getenv("YCSB_AUDIT_PREFIX");
  std::map<std::string,uint64_t> expected;
  std::ofstream trace;
  uint64_t inserts=0,reads=0,updates=0,imported=0;
  static uint64_t Number(const std::string& text) {
    uint64_t value=0;
    const auto result=std::from_chars(text.data(),text.data()+text.size(),value);
    if (text.empty() || result.ec!=std::errc{} || result.ptr!=text.data()+text.size())
      throw std::runtime_error("继承清单数字损坏");
    return value;
  }
  Audit() {
    const char* source=std::getenv("YCSB_AUDIT_IMPORT");
    const char* keys=std::getenv("YCSB_AUDIT_KEYS");
    if (source || keys) {
      if (!prefix || !*prefix || !source || !*source || !keys || !*keys ||
          std::string(source)==std::string(prefix)+".expected")
        throw std::runtime_error("继承清单配置不完整或覆盖源清单");
      const uint64_t count=Number(keys);
      std::ifstream input(source);
      if (!input || !count) throw std::runtime_error("继承清单无法打开或键数为零");
      std::string line,previous;
      while (std::getline(input,line)) {
        const auto space=line.find(' ');
        if (space==std::string::npos || !space || line.find_first_of("\t\r\n")!=std::string::npos ||
            line.find(' ',space+1)!=std::string::npos)
          throw std::runtime_error("继承清单行损坏");
        const std::string key=line.substr(0,space);
        if (!previous.empty() && key<=previous) throw std::runtime_error("继承清单键重复或未排序");
        expected.emplace(key,Number(line.substr(space+1))); previous=key;
        if (expected.size()>count) throw std::runtime_error("继承清单键数超出登记");
      }
      if (!input.eof() || expected.size()!=count) throw std::runtime_error("继承清单键数不符或读取失败");
      imported=count;
    }
    if (prefix) {
      trace.open(std::string(prefix)+".requests",std::ios::binary);
      if (!trace) throw std::runtime_error("请求轨迹无法创建");
    }
  }
};
inline Audit& State() { static Audit audit; return audit; }
inline void Record(char operation,const std::string& key,
                   const std::vector<ycsbc::DB::Field>& values,ycsbc::DB::Status status) {
  auto& a=State(); if (!a.prefix) return;
  if (status!=ycsbc::DB::kOK || values.size()!=1 || values.front().name!="field0")
    throw std::runtime_error("业务失败或不是已登记的单字段完整请求");
  const uint64_t hash=ValueHash(values);
  if (operation=='R') {
    if (!a.expected.count(key) || a.expected.at(key)!=hash) throw std::runtime_error("读取内容与最近成功写入不符");
    ++a.reads;
  } else {
    // 继承模式只允许更新原有键，避免误装载或键格式变化被静默接受。
    if (a.imported && (operation!='U' || !a.expected.count(key)))
      throw std::runtime_error("继承阶段出现插入或未知更新键");
    a.expected[key]=hash;
    if (operation=='I') ++a.inserts; else ++a.updates;
  }
  const uint32_t size=key.size(); const uint64_t write_hash=operation=='R' ? 0 : hash;
  a.trace.put(operation); a.trace.write(reinterpret_cast<const char*>(&size),4);
  a.trace.write(key.data(),key.size()); a.trace.write(reinterpret_cast<const char*>(&write_hash),8);
  if (!a.trace) throw std::runtime_error("请求轨迹写入失败");
}
inline void Save() {
  auto& a=State(); if (!a.prefix) return;
  a.trace.flush();
  std::ofstream expected(std::string(a.prefix)+".expected");
  for (const auto& e:a.expected) expected<<e.first<<' '<<e.second<<'\n';
  expected.close();
  std::ofstream summary(std::string(a.prefix)+".counts");
  summary<<"装载="<<a.inserts<<"\n读取="<<a.reads<<"\n更新="<<a.updates<<"\n键数="<<a.expected.size()<<'\n';
  if (a.imported) summary<<"继承键数="<<a.imported<<'\n';
  summary.close();
  if (!expected || !summary || !a.trace) throw std::runtime_error("校验清单保存失败");
}
}  // 请求审计命名空间。
