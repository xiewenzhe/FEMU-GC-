// 仅验证真实数据路径安全；人工Flush上下文不作为在线分类或收益数据。
#define main previous_colocation_main
#include "colocation_device.cc"
#undef main
#include "rrzc_flush_origin.h"

static uint64_t rc(System& s,uint64_t start) {
  rrzc_v1::RecentReport r;check(s.device->GetZoneRecentReads(&r));
  for(const auto& b:r.zones)if(b.zone==start)return b.rc;
  throw std::runtime_error("缺少物理读状态");
}
int main(int argc,char** argv) {
  try {
    require(argc==2,"用法：尾部安全程序 结果目录");const std::string out=argv[1];
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.log",&logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);auto s=std::make_unique<System>(logger);
    check(s->fs->CreateDir("/tail",IOOptions(),nullptr));
    {rrzc_flush_origin::Scope origin(true,1);write(*s,"/tail/A.sst",MiB,'A',Env::WLTH_SHORT);}
    verify(*s,"/tail/A.sst",MiB,'A');const auto a=extent(*s,"/tail/A.sst");
    auto* zone=s->device->GetIOZone(a.zone_start);const auto wp=zone->wp_,before=rc(*s,a.zone_start);
    check(s->fs->DeleteFile("/tail/A.sst",IOOptions(),nullptr));
    require(!zone->IsUsed() && !zone->IsEmpty() && zone->wp_==wp,"归零后未保留原WP");
    require(rc(*s,a.zone_start)==before,"保留尾部错误重置RC");
    {rrzc_flush_origin::Scope origin(true,2);write(*s,"/tail/B.sst",MiB,'B',Env::WLTH_SHORT);}
    const auto b=extent(*s,"/tail/B.sst");
    require(b.zone_start==a.zone_start && b.start>=wp && zone->wp_>wp,"新文件未沿尾部追加");
    verify(*s,"/tail/B.sst",MiB,'B');const auto new_wp=zone->wp_;
    s.reset();s=std::make_unique<System>(logger);
    zone=s->device->GetIOZone(a.zone_start);require(zone->wp_==new_wp,"重挂载改变有效文件WP");
    verify(*s,"/tail/B.sst",MiB,'B');check(s->fs->DeleteFile("/tail/B.sst",IOOptions(),nullptr));
    bool released=false;check(s->device->ReleasePoolTails(false,&released,"设备安全检查退出"));
    require(released && zone->IsEmpty() && !s->device->ColocationPool()->Contains(a.zone_start),"资源退出未回收空尾部");
    require(rc(*s,a.zone_start)==0,"真实Reset后RC未归零");
    {rrzc_flush_origin::Scope origin(true,3);write(*s,"/tail/C.sst",MiB,'C',Env::WLTH_SHORT);}
    const auto c=extent(*s,"/tail/C.sst");check(s->fs->DeleteFile("/tail/C.sst",IOOptions(),nullptr));
    s.reset();s=std::make_unique<System>(logger);
    require(s->device->GetIOZone(c.zone_start)->IsEmpty(),"关闭后保留尾部未清账");
    std::cout<<"尾部真实安全检查通过：原WP续写、读计数保留、有效内容、重挂载、资源退出和关闭清账\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<"尾部真实检查失败："<<e.what()<<'\n';return 1;}
}
