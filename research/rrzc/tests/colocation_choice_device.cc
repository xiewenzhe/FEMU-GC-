// 多候选四组合机制实验；模型及Greedy由同一对象的最终执行开关选择。
#define RRZC_COLOCATION_CASES_HELPERS
#include "colocation_cases_device.cc"
#undef RRZC_COLOCATION_CASES_HELPERS
static const std::string D="/joint/D.sst",U="/joint/U.sst";
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：多候选程序 smoke|choice 结果目录 0|1");
    const std::string scenario=argv[1],out=argv[2];
    require((scenario=="smoke" || scenario=="choice") &&
        (std::string(argv[3])=="0" || std::string(argv[3])=="1"),"多候选参数非法");
    const bool pooled=std::string(argv[3])=="1";
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.log",&logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);System system(logger);Input input(out+"/请求.csv");
    bool ready=false;
    for(unsigned attempt=0;attempt<50 && !ready;++attempt) {
      logger->Flush();std::ifstream stream(out+"/zenfs.log");
      const std::string text((std::istreambuf_iterator<char>(stream)),std::istreambuf_iterator<char>());
      require(text.find("[ERROR]")==std::string::npos,"维护线程初始化错误，装载前停止");
      ready=text.find("受控GC批次检查：启用=1")!=std::string::npos &&
          text.find("独立近期采样配置：启用=1")!=std::string::npos;
      if(!ready)std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    require(ready,"维护线程未就绪，装载前停止");
    Capture capture(system,logger,out);capture.Save("start");
    check(system.fs->CreateDir("/joint",IOOptions(),nullptr));
    Write(system,input,W,96*MiB,'W',Env::WLTH_SHORT);
    Write(system,input,H,32*MiB,'H',Env::WLTH_LONG);
    Write(system,input,C,96*MiB,'C',Env::WLTH_MEDIUM);
    Write(system,input,D,24*MiB,'D',Env::WLTH_NONE);
    Write(system,input,U,104*MiB,'U',Env::WLTH_NONE);
    const auto h=extent(system,H),w=extent(system,W),c=extent(system,C);
    const auto d=extent(system,D),u=extent(system,U);
    require(pooled?(h.zone_start==w.zone_start && h.zone_start!=c.zone_start):
                   (h.zone_start!=w.zone_start && h.zone_start==c.zone_start),"H/W/C布局不符");
    require(d.zone_start==u.zone_start && d.zone_start!=h.zone_start &&
        system.device->GetIOZone(d.zone_start)->IsFull(),"冷候选未自然写满或意外与热区混放");
    require(system.device->GetIOZone(h.zone_start)->IsFull(),"热源区未自然写满");
    if(scenario=="smoke") {
      capture.Save("prepared");
      for(const auto& item:std::vector<std::pair<std::string,uint64_t>>{{W,96},{H,32},{C,96},{D,24},{U,104}})
        verify(system,item.first,item.second*MiB,item.first[item.first.size()-5]);
      std::cout<<"多候选布局小测通过\n";return 0;
    }
    for(unsigned i=0;i<52;++i) {
      Write(system,input,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i),Env::WLTH_NONE);
      if(i%8==7)std::cout<<"多候选填充："<<i+1<<"/52"<<std::endl;
    }
    require(system.device->GetFreeSpace()==800*MiB && system.device->GetUsedSpace()==7008*MiB,
        "多候选起始空间不符，停止而非改变门槛");
    std::cout<<"等待31秒建立真实窗口；后续显式分段读取用于历史估计，不报告性能"<<std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(31));capture.Save("prepared");
    input.phase="业务";Info(logger,"受控放置业务开始：情形=choice，K=%d",pooled);logger->Flush();
    std::unique_ptr<FSRandomAccessFile> reader;check(system.fs->NewRandomAccessFile(H,options(),&reader,nullptr));
    Buffer buffer;
    for(unsigned batch=0;batch<9;++batch) {
      Read(reader,input,buffer,100);
      input.Call("HISTORY_WAIT","",batch,1000,0,[&]{std::this_thread::sleep_for(std::chrono::seconds(1));});
    }
    input.Call("DELETE",W,0,0,0,[&]{check(system.fs->DeleteFile(W,IOOptions(),nullptr));});
    input.Call("DELETE",U,0,0,0,[&]{check(system.fs->DeleteFile(U,IOOptions(),nullptr));});
    capture.Save("seed");
    const char* gate=getenv("ZENFS_RRZC_CAL_GC_GATE");require(gate && *gate,"缺少共同检查门");
    auto tick=[&](unsigned n){input.Call("CHECK","",n,0,0,[&]{rrzc_front_order::GcTick(gate,n);});};
    tick(1);capture.Save("after-choice");
    Read(reader,input,buffer,312);tick(2);tick(3);reader.reset();capture.Save("business");
    require(system.device->GetUsedSpace()==6808*MiB,"最终有效量与存活请求不符");
    Info(logger,"受控放置业务截止：内容校验另计");logger->Flush();input.phase="校验";
    verify(system,H,32*MiB,'H');verify(system,C,96*MiB,'C');verify(system,D,24*MiB,'D');
    for(unsigned i=0;i<52;++i) {
      verify(system,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i));
      if(i%8==7)std::cout<<"多候选完整校验："<<i+1<<"/52"<<std::endl;
    }
    require(system.fs->FileExists(W,IOOptions(),nullptr).IsNotFound() &&
        system.fs->FileExists(U,IOOptions(),nullptr).IsNotFound(),"退役未生效");
    capture.Save("verified");std::cout<<"多候选四组合验证完成：K="<<pooled<<std::endl;
    return 0;
  }catch(const std::exception& error){std::cerr<<"多候选验证失败："<<error.what()<<'\n';return 1;}
}
