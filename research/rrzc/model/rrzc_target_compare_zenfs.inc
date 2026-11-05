// 此适配仅在独立副本中启用；调用方已独占尚未提交成员的协同目标。
void ZonedBlockDevice::InitTargetCompare() {
  auto flag=[](const char* name) {
    const char* value=getenv(name);
    if(value && strcmp(value,"0") && strcmp(value,"1"))
      throw std::invalid_argument("双目标比较开关只能为0或1");
    return value && !strcmp(value,"1");
  };
  target_compare_=flag("ZENFS_RRZC_POOL_TARGET_COMPARE");
  target_observe_=target_compare_ || flag("ZENFS_RRZC_POOL_TARGET_OBSERVE");
  if(target_observe_ && pair_admission_)
    throw std::invalid_argument("双目标比较不能同时执行旧配对拒绝规则");
  if(target_observe_)pair_observe_=true;
  Info(logger_,"双目标比较配置：执行=%d，采证=%d，仅必要H迁移=1，全部非H计价=1，剩余空间不视为确定收益=1",
       target_compare_,target_observe_);
}

IOStatus ZonedBlockDevice::CompareMigrationTargets(Zone* pool,uint32_t required,
    Env::WriteLifeTimeHint lifetime,Zone** chosen) {
  *chosen=nullptr;
  const uint64_t sequence=target_sequence_.fetch_add(1)+1;
  Zone* ordinary=nullptr;
  unsigned int diff=LIFETIME_DIFF_NOT_GOOD;
  // 复用原普通候选扫描；只取得已有部分区独占，不申请令牌、Finish或改池成员。
  auto status=GetBestOpenZoneMatch(lifetime,&diff,&ordinary,required,nullptr,rrzc_content::kSst);
  if(!status.ok())return status;
  rrzc_target::Target a,b;
  auto snapshot=[&](Zone* z) {
    rrzc_target::Target out;
    if(!z)return out;
    const auto state=z->GetResetState();
    out.zone=z->start_;out.native_valid=z->used_capacity_.load();out.remaining=z->capacity_;
    out.live=pair_ledger_.Read(z->start_);
    out.available=state.known && state.stable && !z->IsEmpty() && !z->IsFull() &&
        !rrzc_colocation::current.sources.count(z->start_);
    return out;
  };
  // 新协同空区也允许保留；普通候选仍须是原扫描取得的已有部分区。
  a=snapshot(pool);a.available=!pool->IsFull() && pool->GetResetState().known &&
      pool->GetResetState().stable && !rrzc_colocation::current.sources.count(pool->start_);
  b=snapshot(ordinary);
  bool ordinary_risk_safe=false;
  if(b.available) {
    rrzc_v1::RecentReport report;
    if(GetZoneRecentReads(&report).ok() && report.threshold) {
      for(const auto& block:report.zones)
        if(block.zone==b.zone)ordinary_risk_safe=block.rc<report.threshold;
    }
  }
  b.available=b.available && ordinary_risk_safe;
  auto decision=rrzc_target::Compare(a,b,required);
  if(!pair_ledger_.Unchanged(a.live.revision) ||
      a.native_valid!=pool->used_capacity_.load() ||
      (ordinary && b.native_valid!=ordinary->used_capacity_.load())) {
    decision={};decision.reason="比较期间映射账目变化，保留协同目标";
  }
  const bool use_ordinary=target_compare_ && decision.ordinary;
  Info(logger_,"双目标比较决定：序号=%lu，协同Zone=%lu，普通存在=%d，普通Zone=%lu，申请=%u，协同有效=%lu，协同账目=%lu，协同H=%lu，协同W=%lu，协同剩余=%lu，普通有效=%lu，普通账目=%lu，普通H=%lu，普通W=%lu，普通剩余=%lu，账目修订=%lu/%lu，普通风险安全=%d，可比=%d，建议普通=%d，执行=%d，实际普通=%d，原因=%s",
       sequence,a.zone,ordinary!=nullptr,b.zone,required,a.native_valid,a.live.total,a.live.h,a.live.w,
       a.remaining,b.native_valid,b.live.total,b.live.h,b.live.w,b.remaining,a.live.revision,b.live.revision,
       ordinary_risk_safe,decision.known,decision.ordinary,target_compare_,use_ordinary,decision.reason);
  if(!ordinary)return IOStatus::OK();
  if(!use_ordinary)return ordinary->CheckRelease();
  // 普通目标沿原ReleaseMigrateZone释放，不登记为协同成员，也不借用其Open令牌。
  if(IsContentGuardEnabled()) {
    const auto before=ordinary->rrzc_content_.load();
    ordinary->rrzc_content_=rrzc_content::kSst;
    Info(logger_,"公共内容分配：阶段=迁移，Zone=%lu，原类别=%u，请求类别=1，实际类别=1",
         ordinary->start_,static_cast<unsigned>(before));
  }
  *chosen=ordinary;
  return IOStatus::OK();
}
