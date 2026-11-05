// 数据库只读校验；严格RR可维护文件系统；进展输出避免把长校验误判为业务卡死。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "rocksdb/db.h"
#include "rocksdb/env.h"
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
using namespace ROCKSDB_NAMESPACE;
static void check(const Status& s) {if(!s.ok())throw std::runtime_error(s.ToString());}
static uint64_t hash(const Slice& value) {
  uint64_t out=14695981039346656037ULL;
  for(size_t i=0;i<value.size();++i){out^=static_cast<unsigned char>(value[i]);out*=1099511628211ULL;}
  return out;
}
int main(int argc,char** argv) {
  try {
    if(argc!=4)throw std::runtime_error("用法：只读校验 posix|zns 数据库或结果目录 最新值清单");
    std::shared_ptr<FileSystem> filesystem;
    std::unique_ptr<Env> env;
    std::string path=argv[2];
    if(std::string(argv[1])=="zns") {
      std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(path+"/zenfs.verify.log",&logger));
      logger->SetInfoLogLevel(DEBUG_LEVEL);
      auto device=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
      check(device->Open(false,true));
      auto fs=std::make_shared<ZenFS>(device.release(),FileSystem::Default(),logger);
      check(fs->Mount(false));filesystem=fs;env=NewCompositeEnv(filesystem);path="/rrzc-ycsb";
    }else if(std::string(argv[1])!="posix")throw std::runtime_error("模式不符");
    Options options;if(env)options.env=env.get();options.create_if_missing=false;
    DB* raw=nullptr;check(DB::OpenForReadOnly(options,path,&raw));std::unique_ptr<DB> db(raw);
    std::unique_ptr<Iterator> iter(db->NewIterator(ReadOptions{}));iter->SeekToFirst();
    std::ifstream manifest(argv[3]);if(!manifest)throw std::runtime_error("清单无法打开");
    uint64_t count=0,expected;std::string key;
    while(manifest>>key>>expected) {
      if(!iter->Valid() || iter->key()!=Slice(key) || hash(iter->value())!=expected)
        throw std::runtime_error("键缺失、多余或最新值错误："+key);
      ++count;iter->Next();
      if(count%65536==0)std::cout<<"校验进度：键数="<<count<<std::endl;
    }
    if(!manifest.eof() || !count || iter->Valid())throw std::runtime_error("清单损坏或键数不符");
    check(iter->status());iter.reset();check(db->Close());
    std::cout<<"只读全键最新值哈希校验通过，键数="<<count<<'\n';
  }catch(const std::exception& e){std::cerr<<"校验失败："<<e.what()<<'\n';return 1;}
}
