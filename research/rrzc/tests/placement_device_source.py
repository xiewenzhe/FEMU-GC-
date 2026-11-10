"""复用已验收RR功能载荷并追加重挂载校验；合成.sst不是RocksDB表。"""
def extend(text):
    assert text.count('int main(int argc,char** argv) {')==1
    assert text.count('  } catch (const std::exception& e) {')==1
    text=text.replace('  } catch (const std::exception& e) {','    return 0;\n  } catch (const std::exception& e) {')
    return text.replace('int main(int argc,char** argv) {','int Exercise(int argc,char** argv) {',1)+r'''
// 首段对象全部析构后再挂载同一模拟盘，核验映射恢复与完整文件内容。
int main(int argc,char** argv) {
  const int result=Exercise(argc,argv);if(result)return result;
  try {
    const std::string out=argv[1];std::shared_ptr<Logger> logger;
    check(Env::Default()->NewLogger(out+"/remount.zenfs.log",&logger));
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(false,true));ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
    FileOptions opt;opt.use_direct_reads=true;Buffer expected,scratch;
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(Name,opt,&reader,nullptr));
    for(unsigned i=0;i<3;++i){Slice data;check(reader->Read(i*MiB,MiB,IOOptions(),&data,scratch.data,nullptr));
      require(data.size()==MiB && !std::memcmp(data.data(),expected.data,MiB),"重挂载内容错误");}
    reader.reset();(void)mapping(fs);check(fs.DeleteFile(Name,IOOptions(),nullptr));
    ZenFSSnapshot snapshot;ZenFSSnapshotOptions options;options.zone_=options.zone_file_=true;
    fs.GetZenFSSnapshot(snapshot,options);
    for(const auto& extent:snapshot.extents_)require(extent.filename!=Name,"删除后仍存在文件映射");
    std::cout<<"重挂载、完整内容和删除映射验证通过\n";
  } catch(const std::exception& error){std::cerr<<"重挂载验证失败："<<error.what()<<'\n';return 1;}
  return 0;
}
'''
