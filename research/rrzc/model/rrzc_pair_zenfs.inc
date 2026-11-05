// 独立副本中的准入适配；调用时持有目标Zone独占，不改变原维护选区。
void ZonedBlockDevice::InitPairAdmission() {
  const char* enabled=getenv("ZENFS_RRZC_POOL_PAIR_ADMISSION");
  if(enabled && strcmp(enabled,"0") && strcmp(enabled,"1"))
    throw std::invalid_argument("配对准入开关只能为0或1");
  pair_admission_=enabled && !strcmp(enabled,"1");
  const char* observe=getenv("ZENFS_RRZC_POOL_PAIR_OBSERVE");
  if(observe && strcmp(observe,"0") && strcmp(observe,"1"))
    throw std::invalid_argument("配对采证开关只能为0或1");
  pair_observe_=pair_admission_ || (observe && !strcmp(observe,"1"));
  const char* budget=getenv("ZENFS_RRZC_POOL_PAIR_COPY_BUDGET");
  if(budget) {
    if(!*budget || std::string(budget).find_first_not_of("0123456789")!=std::string::npos)
      throw std::invalid_argument("伴随复制预算必须为非负整数字节");
    pair_copy_budget_=std::stoull(budget);
  }
  Info(logger_,"配对准入配置：启用=%d，采证=%d，窗口纳秒=%lu，伴随复制预算=%lu，未知寿命不抵扣=1，原选择器不变=1",
       pair_admission_,pair_observe_,rrzc_pair::kHorizon,pair_copy_budget_);
}

bool ZonedBlockDevice::AdmitColocation(Zone* target,uint32_t required,bool new_file) {
  if(!pair_observe_)return true;
  const uint64_t sequence=pair_sequence_.fetch_add(1)+1;
  rrzc_pair::Input input;
  input.live=pair_ledger_.Read(target->start_);input.native_valid=target->used_capacity_.load();
  input.remaining=target->capacity_;input.required=required;input.budget=pair_copy_budget_;
  input.incoming=static_cast<uint8_t>(rrzc_colocation::current.tag);input.new_file=new_file;
  const bool mixed=(input.live.h || (input.incoming&1)) && (input.live.w || (input.incoming&2));
  std::string error;uint64_t session_hi=0,session_lo=0,threshold=0;
  uint64_t device_generation=0,snapshot=0;long double source_rate=0;
  bool frames_ok=true;
  if(mixed) {
    rrzc_v1::RecentReport maximum;
    frames_ok=GetZoneRecentReads(&maximum).ok();
    session_hi=maximum.session_hi;session_lo=maximum.session_lo;threshold=maximum.threshold;
    auto query=[&](uint64_t address,rrzc_all_blocks::Report* output) {
      Zone* z=GetIOZone(address);if(!z)return false;
      const auto before=z->GetResetState();
      if(!before.known || !before.stable)return false;
      const auto anchor=std::find_if(maximum.zones.begin(),maximum.zones.end(),
          [&](const auto& b){return b.zone==address;});
      if(anchor==maximum.zones.end() || !rrzc_all_blocks::QueryPath(GetFilename(),GetNrZones(),
          maximum.blocks_per_zone,anchor->zone_index,GetZoneSize(),output,&error))return false;
      const auto after=z->GetResetState();const auto& first=output->blocks.front();
      // 主机Reset序号与设备代际各自核验，不将两种计数当作同一个数字。
      const bool valid=before.sequence==after.sequence && after.known && after.stable &&
          before.generation==after.generation && output->session_hi==session_hi &&
          output->session_lo==session_lo && output->threshold==threshold &&
          first.generation==anchor->generation && first.epoch_start_ns==anchor->epoch_start_ns &&
          first.snapshot_ns>=anchor->snapshot_ns &&
          first.snapshot_ns-anchor->snapshot_ns<=2*rrzc_v1::kSecond;
      if(valid)for(const auto& b:output->blocks)
        Info(logger_,"配对逐块证据：序号=%lu，目标Zone=%lu，角色=%s，Zone=%lu，会话=%lu/%lu，设备代际=%lu，主机Reset序号=%lu，快照=%lu，代际起点=%lu，块=%u，PPA=%u/%u/%u/%u，RC=%lu，十秒增量=%lu，三十秒增量=%lu，标志=%u，阈值=%lu",
             sequence,target->start_,address==target->start_?"目标":"H源",address,session_hi,session_lo,
             b.generation,after.sequence,b.snapshot_ns,b.epoch_start_ns,b.block_id,b.ch,b.lun,b.plane,
             b.block,b.rc,b.delta10,b.delta30,b.flags,threshold);
      return valid;
    };
    if(frames_ok && (input.incoming&1)) {
      input.incoming_h_known=!rrzc_colocation::current.sources.empty();
      for(auto source:rrzc_colocation::current.sources) {
        rrzc_all_blocks::Report frame;
        if(!query(source,&frame)){input.incoming_h_known=false;break;}
        for(const auto& block:frame.blocks) {
          long double rate=0;
          if(!rrzc_pair::Rate(block,&rate)){input.incoming_h_known=false;break;}
          // 整个源区的历史速率包含非H读取；作为保守情景披露，不称精确H速率。
          source_rate+=rate;
        }
        if(!input.incoming_h_known)break;
      }
      if(source_rate<=0)input.incoming_h_known=false;
    }
    if(frames_ok) {
      rrzc_all_blocks::Report frame;frames_ok=query(target->start_,&frame);
      if(frames_ok) {
        device_generation=frame.blocks.front().generation;snapshot=frame.blocks.front().snapshot_ns;
        input.risk=rrzc_pair::Forecast(frame.blocks,threshold,source_rate);
      }
    }
  }
  auto decision=rrzc_pair::Evaluate(input);
  if(!pair_ledger_.Unchanged(input.live.revision) || input.native_valid!=target->used_capacity_.load()) {
    decision.allow=false;decision.reason="查询期间有效映射账目变化，回退重试原分配";
  }
  Info(logger_,"配对准入决定：序号=%lu，阶段=%s，Zone=%lu，类别=%u，有效=%lu，账目有效=%lu，H=%lu，W=%lu，HW=%lu，剩余=%lu，申请=%u，新文件容量上界=%d，共区=%d，会话=%lu/%lu，设备代际=%lu，快照=%lu，查询完整=%d，H源压力可知=%d，H源每秒速率=%.6Lf，风险可知=%d，代表块=%u，RC=%lu，阈值=%lu，ETA=%lu，窗口=%lu，伴随复制上界=%lu，预算=%lu，允许=%d，原因=%s，查询错误=%s，执行准入=%d",
       sequence,new_file?"新文件":"必要迁移",target->start_,input.incoming,input.native_valid,input.live.total,
       input.live.h,input.live.w,input.live.both,input.remaining,required,new_file,decision.mixed,
       session_hi,session_lo,device_generation,snapshot,frames_ok,input.incoming_h_known,
       source_rate*rrzc_v1::kSecond,input.risk.known,input.risk.block,input.risk.rc,threshold,
       input.risk.eta,rrzc_pair::kHorizon,decision.companion_upper,input.budget,decision.allow,
       decision.reason,error.c_str(),pair_admission_);
  return !pair_admission_ || decision.allow;
}
