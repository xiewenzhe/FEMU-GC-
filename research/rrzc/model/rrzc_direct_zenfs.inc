// 此片段仅由默认关闭的统一入口调用；不调用Greedy建议或旧预算执行器。
{
  std::map<uint64_t,rrzc_retention::Forecast> forecasts;
  for (const auto& c:candidates) {
    auto point=retention_current.zones.find(c.zone);
    rrzc_retention::Forecast f;
    if (frame_ok && usable.count(c.zone) && point!=retention_current.zones.end() && point->second.valid==c.valid)
      f=retention_frozen.Check(retention_current,point->second,shadow_horizon);
    forecasts.emplace(c.zone,f);
    Info(logger_,"统一留存：轮次=%lu，Zone=%lu，可知=%d，十秒减少=%lu，三十秒减少=%lu，十秒间隔=%lu，三十秒间隔=%lu",
        shadow_round,c.zone,f.known,f.loss10,f.loss30,f.interval10,f.interval30);
  }
  auto ranked=rrzc_direct::Rank(candidates,usable,forecasts,recent.threshold,simple_budget,shadow_horizon);
  const auto reserve=zbd_->GetMigrationReserveObservation();
  const auto spare=zbd_->GetMigrationSpareObservation();
  const auto spare_snapshot=std::find_if(snapshot.zones_.begin(),snapshot.zones_.end(),
      [&](const ZoneSnapshot& z){return spare.present && z.start==spare.zone;});
  const uint64_t spare_capacity=spare_snapshot==snapshot.zones_.end() ? 0 : spare_snapshot->max_capacity;
  uint64_t empty_now=0;
  // 普通空目标同样可用，不再要求必须适配当前预留尾部；原分配器仍处理竞争。
  for (const auto& z:snapshot.zones_) {
    Zone* live=zbd_->GetIOZone(z.start);
    if (live && live->Acquire()) {
      if (live->wp_==z.start && live->used_capacity_.load()==0 && live->IsEmpty())
        empty_now=std::max(empty_now,z.max_capacity);
      live->Release();
    }
  }
  for (auto& row:ranked) {
    const auto& c=row.candidate;const auto& f=forecasts.at(c.zone);
    rrzc_guard::Bounds bounds;
    for (const auto& file:snapshot.zone_files_) for (const auto& ext:file.extents)
      if (ext.zone_start==c.zone) bounds.Add(ext.length,zbd_->GetBlockSize(),ZoneFile::SPARSE_HEADER_SIZE,c.capacity);
    const auto rechecked=zbd_->GetMigrationReserveObservation();
    const auto spare_rechecked=zbd_->GetMigrationSpareObservation();
    const bool spare_ok=rrzc_direct::SpareFits(c.zone,bounds,c.valid,spare_capacity,spare,spare_rechecked);
    const bool reserve_ok=rrzc_guard::StableReserve(reserve,rechecked) &&
        reserve.zone!=c.zone && (bounds.upper<=reserve.remaining || (!reserve.valid && bounds.upper<=c.capacity));
    const bool destination=bounds.Complete(c.valid) && (reserve_ok || spare_ok || empty_now>=bounds.upper);
    Info(logger_,"统一替补复核：轮次=%lu，Zone=%lu，容量=%lu，启用=%d/%d，可知=%d/%d，存在=%d/%d，替补Zone=%lu/%lu，剩余=%lu/%lu，有效=%lu/%lu，写指针=%lu/%lu，代际=%lu/%lu，序号=%lu/%lu，通过=%d",
        shadow_round,c.zone,spare_capacity,spare.enabled,spare_rechecked.enabled,spare.known,spare_rechecked.known,
        spare.present,spare_rechecked.present,spare.zone,spare_rechecked.zone,spare.remaining,spare_rechecked.remaining,
        spare.valid,spare_rechecked.valid,spare.wp,spare_rechecked.wp,spare.host_generation,spare_rechecked.host_generation,
        spare.host_sequence,spare_rechecked.host_sequence,spare_ok);
    Zone* live=zbd_->GetIOZone(c.zone);
    const auto first=diagnostic_before.find(c.zone);
    const auto last=recent_after.find(c.zone);
    const auto original=std::find_if(snapshot.zones_.begin(),snapshot.zones_.end(),
        [&](const ZoneSnapshot& z){return z.start==c.zone;});
    const auto elapsed=std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now()-query_start).count();
    bool source=false;
    if (live && first!=diagnostic_before.end() && last!=recent_after.end() && original!=snapshot.zones_.end()) {
      const auto state=live->GetResetState();
      source=elapsed<=static_cast<int64_t>(2*rrzc_v1::kSecond) && live->wp_==original->wp &&
          live->used_capacity_.load()==c.valid && IsMaintenanceWindowStable(first->second,last->second,state);
      Info(logger_,"统一源复核：轮次=%lu，Zone=%lu，经过纳秒=%ld，原写指针=%lu，当前写指针=%lu，原有效=%lu，当前有效=%lu，稳定=%d/%d/%d，代际=%lu/%lu/%lu，序号=%lu/%lu/%lu，通过=%d",
          shadow_round,c.zone,static_cast<long>(elapsed),original->wp,live->wp_,c.valid,live->used_capacity_.load(),
          first->second.known && first->second.stable,last->second.known && last->second.stable,state.known && state.stable,
          first->second.generation,last->second.generation,state.generation,
          first->second.sequence,last->second.sequence,state.sequence,source);
    }
    row.safe=source && destination;
    Info(logger_,"统一成本：轮次=%lu，Zone=%lu，有效=%lu，回收=%lu，风险可知=%d，ETA=%lu，预计留存=%lu，RR抵扣=%lu，净复制=%lu，覆盖=%d，物理上界=%lu，空目标=%lu，预留稳定=%d，预留剩余=%lu，预留有效=%lu，源稳定=%d，安全=%d，留存可知=%d，替补可用=%d",
        shadow_round,c.zone,c.valid,row.cost.reclaimed,row.cost.risk_known,row.cost.eta,row.cost.retained,
        row.cost.credit,row.cost.net,bounds.Complete(c.valid),bounds.upper,empty_now,reserve_ok,
        reserve.remaining,reserve.valid,source,row.safe,f.known,spare_ok);
  }
  decision={};
  for (const auto& row:ranked) if (row.safe) {
    decision.found=true;decision.selected=row.candidate;break;
  }
  // 不安全的第一名只跳过自身，继续按同一模型排序；不回退Greedy。
  v1_selected=decision.found;v1_switched=false;v1_extra_valid=0;
  v1_actual_zone=decision.found ? decision.selected.zone : 0;
  Info(logger_,"统一决策：轮次=%lu，候选数=%zu，排序数=%zu，实际存在=%d，实际Zone=%lu，界限纳秒=%lu，回收资格=%lu，先选Greedy=0",
      shadow_round,candidates.size(),ranked.size(),decision.found,v1_actual_zone,shadow_horizon,simple_budget);
}
