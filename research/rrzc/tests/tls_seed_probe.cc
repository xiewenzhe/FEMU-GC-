// 使用真实RocksDB随机数实现验证：线程身份不同，同角色的内部序列仍一致。
#include "rrzc_tls_seed.h"
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>

int main() {
  try {
    std::atomic<unsigned> ready{0};uint64_t hashes[2]={};std::thread::id ids[2];
    auto run=[&](unsigned index) {
      ids[index]=std::this_thread::get_id();++ready;
      while(ready.load()!=2)std::this_thread::yield();
      rrzc_tls_seed::Apply(20261023,0);
      uint64_t hash=1469598103934665603ULL;
      for(unsigned n=0;n<100000;++n)
        hash=(hash^ROCKSDB_NAMESPACE::Random::GetTLSInstance()->Next())*1099511628211ULL;
      hashes[index]=hash;
    };
    std::thread first(run,0),second(run,1);first.join();second.join();
    if(ids[0]==ids[1] || hashes[0]!=hashes[1] || rrzc_tls_seed::Seed(20261023,0)==rrzc_tls_seed::Seed(20261023,1))
      throw std::runtime_error("线程身份或内部序列核验失败");
    ROCKSDB_NAMESPACE::Random expected(rrzc_tls_seed::Seed(20261023,0));uint64_t hash=1469598103934665603ULL;
    for(unsigned n=0;n<100000;++n)hash=(hash^expected.Next())*1099511628211ULL;
    if(hash!=hashes[0])throw std::runtime_error("线程本地随机数未使用指定种子");
    std::cout<<"内部随机种子回归通过：不同线程同角色序列一致，角色种子分离，真实RocksDB实现\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<"内部随机种子回归失败："<<e.what()<<'\n';return 1;}
}
