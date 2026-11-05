// 调用前已取得候选Zone独占权及原Reset管理锁；不增加设备资源配额。
bool ZonedBlockDevice::KeepPoolTail(Zone* zone) {
  if(!rrzc_pool_tail::Observe() || !coloc_pool_ || !coloc_pool_->Contains(zone->start_))return false;
  rrzc_pool_tail::Input x;
  x.member=true;x.used=zone->IsUsed();x.empty=zone->IsEmpty();x.full=zone->IsFull();
  x.remaining=zone->capacity_;x.alignment=GetBlockSize();
  const auto reset=zone->GetResetState();x.stable=reset.known && reset.stable;
  x.migrating=migrating_.load();x.reserve_ready=migration_reserve_zone_ &&
      (!migration_spare_enabled_ || migration_spare_zone_);
  x.waiters=allocation_pressure_.Count();x.active=active_io_zones_.load();x.limit=max_nr_active_io_zones_;
  rrzc_v1::RecentReport report;
  if(GetZoneRecentReads(&report).ok()) {
    x.threshold=report.threshold;
    for(const auto& b:report.zones)if(b.zone==zone->start_) {
      x.rc=b.rc;x.risk_known=(b.flags&rrzc_v1::kEnabled)!=0;break;
    }
  }
  const bool eligible=rrzc_pool_tail::Keep(x),keep=eligible && rrzc_pool_tail::Enabled();
  if(keep){std::lock_guard<std::mutex> lock(pool_tail_mutex_);pool_tail_held_.insert(zone->start_);}
  Info(logger_,"协同尾部归零：Zone=%lu，WP=%lu，剩余=%lu，有效=%lu，主机代际=%lu，RC已知=%d，RC=%lu，阈值=%lu，Active=%lu，上限=%lu，等待=%lu，迁移中=%d，预留可用=%d，符合保留=%d，执行保留=%d",
       zone->start_,zone->wp_,x.remaining,zone->used_capacity_.load(),reset.generation,
       x.risk_known,x.rc,x.threshold,x.active,x.limit,x.waiters,x.migrating,x.reserve_ready,eligible,keep);
  return keep;
}

bool ZonedBlockDevice::ForgetPoolTail(uint64_t start) {
  std::lock_guard<std::mutex> lock(pool_tail_mutex_);return pool_tail_held_.erase(start)!=0;
}

// 资源不足和进程关闭均可退出保留；try锁避免在原分配锁序内引入等待环。
IOStatus ZonedBlockDevice::ReleasePoolTails(bool only_without_empty,bool* released,const char* reason) {
  *released=false;
  if(!rrzc_pool_tail::Enabled() || !coloc_pool_)return IOStatus::OK();
  {std::lock_guard<std::mutex> lock(pool_tail_mutex_);if(pool_tail_held_.empty())return IOStatus::OK();}
  std::unique_lock<std::mutex> migrate(migrate_zone_mtx_,std::try_to_lock);
  if(!migrate.owns_lock())return IOStatus::OK();
  std::unique_lock<std::mutex> reserve(migration_reserve_mtx_,std::try_to_lock);
  if(!reserve.owns_lock())return IOStatus::OK();
  if(only_without_empty)for(auto* z:io_zones) {
    if(!z->Acquire())continue;
    const bool empty=z->IsEmpty();const auto s=z->CheckRelease();if(!s.ok())return s;
    if(empty)return IOStatus::OK();
  }
  for(auto* z:io_zones) {
    bool held=false;{std::lock_guard<std::mutex> lock(pool_tail_mutex_);held=pool_tail_held_.count(z->start_)!=0;}
    if(!held || !z->Acquire())continue;
    if(z->IsUsed() || z->IsEmpty()) {ForgetPoolTail(z->start_);const auto s=z->CheckRelease();if(!s.ok())return s;continue;}
    const bool active=!z->IsFull();const auto wp=z->wp_,left=z->capacity_;
    auto s=z->Reset();if(s.ok() && active)PutActiveIOZoneToken();
    const auto unlock=z->CheckRelease();
    Info(logger_,"协同尾部退出：原因=%s，Zone=%lu，旧WP=%lu，旧剩余=%lu，成功=%d，归还Active=%d",
         reason,z->start_,wp,left,s.ok(),s.ok() && active);
    if(!s.ok())return s;
    if(!unlock.ok())return unlock;
    *released=true;
  }
  return IOStatus::OK();
}
