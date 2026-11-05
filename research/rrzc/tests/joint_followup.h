// 独立后续压力测试：相同真实写入检验先前少搬的数据是否只被推迟。
// 不向模型提供未来信息，不直接调用任何GC、RR、Finish或Reset。
#pragma once
static const std::string FollowupFile="/v1-execution-smoke/followup.bin";
static void followup_snapshot(ZenFS& fs,ZonedBlockDevice& d,const std::string& out,
                              const char* phase) {
  std::ofstream summary(out+"/followup.space.csv",std::ios::app);
  if(summary.tellp()==0) summary<<"阶段,空闲字节,有效字节,可回收字节,冷当前Zone,热当前Zone\n";
  summary<<phase<<','<<d.GetFreeSpace()<<','<<d.GetUsedSpace()<<','<<d.GetReclaimableSpace()<<','
         <<extent(fs,joint_file(0),4*MiB).zone_start<<','
         <<extent(fs,joint_file(1),5*MiB).zone_start<<'\n';
  summary.close();require(summary.good(),"后续空间记录失败");
  ZenFSSnapshot snapshot;ZenFSSnapshotOptions options;options.zone_=true;
  fs.GetZenFSSnapshot(snapshot,options);
  std::ofstream zones(out+"/followup.zones.csv",std::ios::app);
  if(zones.tellp()==0) zones<<"阶段,Zone,写指针,剩余容量,最大容量,有效字节\n";
  for(const auto& z:snapshot.zones_)
    zones<<phase<<','<<z.start<<','<<z.wp<<','<<z.capacity<<','<<z.max_capacity<<','<<z.used_capacity<<'\n';
  zones.close();require(zones.good(),"后续主机Zone快照记录失败");
}
static void followup_run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                         const std::string& out) {
  followup_snapshot(fs,d,out,"写入前");
  Info(logger,"后续空间压力开始：文件=%s，写入字节=%lu，载荷种子=180，写后等待纳秒=25000000000",
       FollowupFile.c_str(),JointZone);logger->Flush();
  FileOptions options;options.use_direct_reads=options.use_direct_writes=true;Buffer data;
  const auto begin=Clock::now();
  write(fs,FollowupFile,JointZone,180,Env::WLTH_MEDIUM,data,options);
  const auto written=Clock::now();
  Info(logger,"后续空间写入完成：实际字节=%lu，API阶段纳秒=%ld",JointZone,
       static_cast<long>(std::chrono::duration_cast<std::chrono::nanoseconds>(written-begin).count()));
  followup_snapshot(fs,d,out,"写入后");
  // 两组都从本次写入完成起等待同样时长，仅由原十秒GC线程自然维护。
  std::this_thread::sleep_until(written+std::chrono::seconds(25));
  followup_snapshot(fs,d,out,"等待后");
  Info(logger,"后续空间压力结束：计划写入字节=%lu，计划写后等待纳秒=25000000000，实际写后纳秒=%ld",
       JointZone,static_cast<long>(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-written).count()));
  logger->Flush();std::cout<<"后续空间压力完成：同样写入128MiB，未强制选区或调用GC"<<std::endl;
}
static void followup_verify(ZenFS& fs) {
  FileOptions options;options.use_direct_reads=true;Buffer expected,scratch;expected.fill(180);
  uint64_t bytes=0;check(fs.GetFileSize(FollowupFile,IOOptions(),&bytes,nullptr));
  require(bytes==JointZone,"后续文件长度错误");
  std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(FollowupFile,options,&reader,nullptr));
  for(uint64_t off=0;off<bytes;off+=MiB) {
    Slice result;check(reader->Read(off,MiB,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==MiB && !std::memcmp(result.data(),expected.get(),MiB),"后续文件全量校验失败");
  }
  std::cout<<"后续文件完整载荷校验通过：128MiB，种子180"<<std::endl;
}
