// 边界实验：固定H/D大小，仅改变填充量和D退役的逻辑时点；不是性能负载。
#define RRZC_COLOCATION_CASES_HELPERS
#include "colocation_cases_device.cc"
#undef RRZC_COLOCATION_CASES_HELPERS
static const std::string D="/joint/D.sst",U="/joint/U.sst";
int main(int argc,char** argv) {
  try {
    require(argc==4 && std::string(argv[3])=="1","用法：边界实验 smoke|low-early|low-late|low-live|high-early|high-late|high-live 目录 1");
    const std::string scenario=argv[1],out=argv[2];
    const bool smoke=scenario=="smoke";
    const bool high=scenario.rfind("high-",0)==0;
    const bool early=scenario=="low-early" || scenario=="high-early";
    const bool late=scenario=="low-late" || scenario=="high-late";
    const bool live=scenario=="low-live" || scenario=="high-live";
    require(smoke || ((high || scenario.rfind("low-",0)==0) && (early || late || live)),"边界情形非法");
    const unsigned fillers=high?51:47;
    const uint64_t prepared_valid=6019*MiB+MiB/2+(high?4*Z:0);
    const uint64_t prepared_free=(high?1024:1536)*MiB;
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.log",&logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);System system(logger);Input input(out+"/请求.csv");
    bool ready=false;
    for(unsigned n=0;n<50 && !ready;++n) {
      logger->Flush();std::ifstream stream(out+"/zenfs.log");
      const std::string text((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
      require(text.find("[ERROR]")==std::string::npos,"维护初始化错误，装载前停止");
      ready=text.find("受控GC批次检查：启用=1")!=std::string::npos &&
          text.find("独立近期采样配置：启用=1")!=std::string::npos;
      if(!ready)std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    require(ready,"维护线程未就绪");Capture capture(system,logger,out);capture.Save("start");
    check(system.fs->CreateDir("/joint",IOOptions(),nullptr));
    Write(system,input,W,126*MiB,'W',Env::WLTH_SHORT);
    Write(system,input,H,2*MiB,'H',Env::WLTH_LONG);
    // 原资格排除舍入为100%的无效比例；1.5MiB保持真实正有效量且不触发该舍入。
    Write(system,input,D,3*MiB/2,'D',Env::WLTH_NONE);
    Write(system,input,U,253*MiB/2,'U',Env::WLTH_NONE);
    const auto h=extent(system,H),w=extent(system,W),d=extent(system,D),u=extent(system,U);
    require(h.zone_start==w.zone_start && d.zone_start==u.zone_start && h.zone_start!=d.zone_start &&
        system.device->GetIOZone(h.zone_start)->IsFull() && system.device->GetIOZone(d.zone_start)->IsFull(),
        "双候选没有自然满区，拒绝改变分配或强制Finish");
    if(smoke) {
      capture.Save("prepared");verify(system,W,126*MiB,'W');verify(system,H,2*MiB,'H');
      verify(system,D,3*MiB/2,'D');verify(system,U,253*MiB/2,'U');
      std::cout<<"边界布局小测通过\n";return 0;
    }
    for(unsigned i=0;i<fillers;++i) {
      Write(system,input,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i),Env::WLTH_NONE);
      if(i%8==7)std::cout<<"校准填充："<<i+1<<"/"<<fillers<<std::endl;
    }
    input.Call("DELETE",W,0,0,0,[&]{check(system.fs->DeleteFile(W,IOOptions(),nullptr));});
    input.Call("DELETE",U,0,0,0,[&]{check(system.fs->DeleteFile(U,IOOptions(),nullptr));});
    require(system.device->GetFreeSpace()==prepared_free && system.device->GetUsedSpace()==prepared_valid,
        "20%附近的空间起点不符，停止而非调整资格");
    std::cout<<"等待31秒形成退役后的真实历史；未向模型提供未来D的标签"<<std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(31));capture.Save("prepared");
    input.phase="业务";Info(logger,"受控放置业务开始：情形=%s，K=1",scenario.c_str());logger->Flush();
    std::unique_ptr<FSRandomAccessFile> reader;check(system.fs->NewRandomAccessFile(H,options(),&reader,nullptr));
    Buffer buffer;
    for(unsigned batch=0;batch<9;++batch) {
      Read(reader,input,buffer,100);
      input.Call("HISTORY_WAIT","",batch,1000,0,[&]{std::this_thread::sleep_for(std::chrono::seconds(1));});
    }
    capture.Save("seed");const char* gate=getenv("ZENFS_RRZC_CAL_GC_GATE");require(gate && *gate,"检查门缺失");
    auto tick=[&](unsigned n){input.Call("CHECK","",n,0,0,[&]{rrzc_front_order::GcTick(gate,n);});};
    tick(1);capture.Save("after-choice");
    // 未来时点只决定客户端真实Delete顺序，不作为模型标签或评分参数。
    if(early)input.Call("DELETE",D,0,0,0,[&]{check(system.fs->DeleteFile(D,IOOptions(),nullptr));});
    else input.Call("KEEP",D,0,0,0,[]{});
    Read(reader,input,buffer,312);tick(2);capture.Save("early");
    // 固定后续写入重新施加空间压力，暴露被推迟的D维护；不直接调用GC或指定victim。
    for(unsigned i=0;i<2;++i)Write(system,input,"/joint/tail"+std::to_string(i)+".bin",Z,'T'+i,Env::WLTH_NONE);
    tick(3);capture.Save("after-write");
    if(late)input.Call("DELETE",D,0,0,0,[&]{check(system.fs->DeleteFile(D,IOOptions(),nullptr));});
    else input.Call("KEEP",D,0,0,0,[]{});
    capture.Save("after-lifetime");
    // 增加完整一轮阈值量的真实读取，避免仅以首个短窗口中RR=0作为收益。
    Read(reader,input,buffer,1024);tick(4);reader.reset();capture.Save("business");
    require(system.device->GetUsedSpace()==prepared_valid+Z*2-(live?0:3*MiB/2),"终点存活量不符");
    Info(logger,"受控放置业务截止：内容校验另计");logger->Flush();input.phase="校验";
    verify(system,H,2*MiB,'H');
    if(live)verify(system,D,3*MiB/2,'D');
    else require(system.fs->FileExists(D,IOOptions(),nullptr).IsNotFound(),"D退役未生效");
    for(unsigned i=0;i<fillers;++i) {
      verify(system,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i));
      if(i%8==7)std::cout<<"校准完整校验："<<i+1<<"/"<<fillers<<std::endl;
    }
    for(unsigned i=0;i<2;++i)verify(system,"/joint/tail"+std::to_string(i)+".bin",Z,'T'+i);
    capture.Save("verified");std::cout<<"边界实验完成："<<scenario<<std::endl;return 0;
  }catch(const std::exception& error){std::cerr<<"边界实验失败："<<error.what()<<'\n';return 1;}
}
