// 使用真实CoreWorkload和内存记录接口检查请求序列；这不是SSD实验。
#include "core_workload.h"
#include <iostream>

class TraceDB : public ycsbc::DB {
 public:
  Status Read(const std::string &, const std::string &key,
      const std::vector<std::string> *, std::vector<Field> &) override {
    std::cout << "R " << key << '\n'; return kOK;
  }
  Status Update(const std::string &, const std::string &key, std::vector<Field> &values) override {
    uint64_t hash = 14695981039346656037ULL;
    for (const auto &f : values) for (unsigned char c : f.value) hash = (hash ^ c) * 1099511628211ULL;
    std::cout << "U " << key << ' ' << hash << '\n'; return kOK;
  }
  Status Scan(const std::string &, const std::string &, int,
      const std::vector<std::string> *, std::vector<std::vector<Field>> &) override { return kError; }
  Status Insert(const std::string &, const std::string &, std::vector<Field> &) override { return kError; }
  Status Delete(const std::string &, const std::string &) override { return kError; }
};

int main(int argc, char **argv) {
  ycsbc::utils::Properties p;
  for (const auto &kv : std::vector<std::pair<std::string, std::string>>{
      {"recordcount", "1024"}, {"operationcount", "20000"}, {"fieldcount", "1"},
      {"fieldlength", "16"}, {"insertorder", "ordered"}, {"zeropadding", "20"},
      {"readproportion", "0.8"}, {"updateproportion", "0.2"},
      {"writeallfields", "true"}, {"rrzc.read_key_count", "64"}})
    p.SetProperty(kv.first, kv.second);
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i]; const auto at = arg.find('=');
    if (at == std::string::npos) return 2;
    p.SetProperty(arg.substr(0, at), arg.substr(at + 1));
  }
  try {
    ycsbc::CoreWorkload workload; workload.Init(p); TraceDB db;
    for (int i = 0; i < 20000; ++i) if (!workload.DoTransaction(db)) return 3;
  } catch (const std::exception &e) {
    std::cerr << "配置或生成失败：" << e.what() << '\n'; return 1;
  }
}
