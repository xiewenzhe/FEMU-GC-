// 放在独立zbd翻译单元的rocksdb命名空间中；仅显式K=1时启用。
void ZonedBlockDevice::InitColocationPool() {
  const char* k=getenv("ZENFS_RRZC_POOL_K");
  if(!k || !strcmp(k,"0"))return;
  if(strcmp(k,"1"))throw std::invalid_argument("原型暂只支持K=0或K=1");
  const char* path=getenv("ZENFS_RRZC_POOL_TAGS");
  if(!path || !path[0])throw std::invalid_argument("K=1需要显式受控标签清单");
  std::ifstream input(path);
  if(!input)throw std::invalid_argument("无法读取受控标签清单");
  std::string line;
  while(std::getline(input,line)) {
    if(line.empty())continue;
    std::istringstream row(line);std::string name,tag,extra;
    if(!(row>>name>>tag) || (row>>extra) || name.empty() || name[0]!='/' ||
        name.size()<4 || name.substr(name.size()-4)!=".sst")
      throw std::invalid_argument("标签清单必须为绝对SST路径及H/W/HW");
    const uint8_t mask=tag=="H"?1:tag=="W"?2:tag=="HW"?3:0;
    if(!mask || !coloc_tags_.emplace(name,mask).second)
      throw std::invalid_argument("标签重复或类别不合法");
  }
  if(coloc_tags_.empty() || max_nr_active_io_zones_<2)
    throw std::invalid_argument("协同池标签为空或普通Active预算不足");
  coloc_pool_=std::make_unique<rrzc_colocation::Pool>(1);
  Info(logger_,"协同池配置：K=1，普通Active预算=%u，标签来源=受控清单，新文件及必要迁移=1，元数据扩展=10",
       max_nr_active_io_zones_);
}

uint8_t ZonedBlockDevice::ColocationTag(const std::string& name) const {
  const auto p=coloc_tags_.find(name);return p==coloc_tags_.end()?0:p->second;
}

std::vector<rrzc_colocation::Zone> ZonedBlockDevice::ColocationObservation(bool risk) {
  using rrzc_colocation::State;
  std::map<uint64_t,uint64_t> counts;uint64_t threshold=0;
  if(risk) {
    rrzc_v1::RecentReport report;
    if(GetZoneRecentReads(&report).ok()) {
      threshold=report.threshold;
      for(const auto& b:report.zones)counts[b.zone]=b.rc;
    }
  }
  std::vector<rrzc_colocation::Zone> result;
  for(auto* z:io_zones) {
    rrzc_colocation::Zone row;row.id=z->start_;
    const auto generation=z->GetResetState();row.generation=generation.generation;
    // 未取得独占时不读取WP/容量等非原子字段，池成员保持未知而非视为已Full。
    if(!z->Acquire()) {row.busy=true;result.push_back(row);continue;}
    row.remaining=z->capacity_;
    row.state=z->IsEmpty()?State::kEmpty:z->IsFull()?State::kFull:State::kClosed;
    row.content_compatible=rrzc_content::Claimable(z->rrzc_content_.load(),rrzc_content::kSst);
    row.risk_safe=!risk || (threshold && counts.count(row.id) && counts.at(row.id)<threshold);
    if(!generation.known || !generation.stable)row.risk_safe=false;
    const auto released=z->CheckRelease();
    if(!released.ok())throw std::runtime_error(released.ToString());
    result.push_back(row);
  }
  return result;
}

IOStatus ZonedBlockDevice::RestoreColocation(const std::map<uint64_t,uint8_t>& roles) {
  if(!coloc_pool_)return IOStatus::OK();
  try {
    // Open阶段尚未恢复文件角色，原预留管理可能先接管某个协同部分区。
    // 用现有空替补交换角色，旧部分区归还普通Active预算；不Finish、不复制数据。
    {
      std::lock_guard<std::mutex> lock(migration_reserve_mtx_);
      auto* old=migration_reserve_zone_;
      if(old && roles.count(old->start_)) {
        Zone* replacement=migration_spare_zone_;
        if(!replacement) {
          for(auto* zone:io_zones) {
            if(!zone->Acquire())continue;
            if(zone->IsEmpty()){replacement=zone;break;}
            const auto released=zone->CheckRelease();if(!released.ok())return released;
          }
        }
        if(!replacement)return IOStatus::NoSpace("协同池恢复缺少安全空预留，停止而非Finish数据区");
        const bool token=!old->IsFull() && !old->IsEmpty();
        if(token && !GetActiveIOZoneTokenIfAvailable()) {
          if(replacement!=migration_spare_zone_)replacement->CheckRelease();
          return IOStatus::NoSpace("协同池恢复没有普通Active预算，停止而非借用维护名额");
        }
        migration_reserve_zone_=replacement;
        if(replacement==migration_spare_zone_)migration_spare_zone_=nullptr;
        const auto released=old->CheckRelease();if(!released.ok())return released;
        Info(logger_,"协同池恢复预留交接：旧Zone=%lu，新预留Zone=%lu，普通Active新增=%d，复制=0，Finish=0",
             old->start_,replacement->start_,token);
      }
    }
    const auto reserve=EnsureMigrationReserve();if(!reserve.ok())return reserve;
    const auto observation=ColocationObservation(false);
    std::vector<rrzc_colocation::Member> members;
    for(const auto& item:roles) {
      auto* z=GetIOZone(item.first);
      if(!z || !item.second || item.second>3)return IOStatus::Corruption("协同池恢复成员无效");
      members.push_back({item.first,z->GetResetState().generation,item.second});
    }
    coloc_pool_->Restore(members,observation);
    Info(logger_,"协同池恢复：成员=%zu，仅依据已恢复文件的当前有效映射，不恢复旧内存指针",
         coloc_pool_->Snapshot().size());
    return IOStatus::OK();
  }catch(const std::exception& error){return IOStatus::Corruption(error.what());}
}

IOStatus ZonedBlockDevice::TryColocation(uint32_t required,Env::WriteLifeTimeHint lifetime,
                                       bool own_open,Zone** output) {
  *output=nullptr;
  if(!coloc_pool_ || rrzc_colocation::current.tag==rrzc_colocation::Tag::kNormal)
    return IOStatus::OK();
  auto observed=ColocationObservation(true);
  rrzc_colocation::Resources resources;
  resources.ordinary_active_budget=max_nr_active_io_zones_;
  const long active_now=active_io_zones_.load(),active_limit=max_nr_active_io_zones_;
  resources.free_active=active_now<active_limit?active_limit-active_now:0;
  const long open_limit=max_nr_open_io_zones_-1,open_now=open_io_zones_.load();
  resources.free_open=own_open?1:(open_now<open_limit?open_limit-open_now:0);
  const auto plan=coloc_pool_->Choose(observed,resources,rrzc_colocation::current.tag,required,
                                    rrzc_colocation::current.sources);
  if(plan.action==rrzc_colocation::Action::kFallback) {
    Info(logger_,"协同池回退：类别=%u，原因=%s，不等待配对=1",
         static_cast<unsigned>(plan.tag),plan.reason.c_str());return IOStatus::OK();
  }
  auto* z=GetIOZone(plan.zone);
  if(!z || !z->Acquire())return IOStatus::OK();
  const bool fresh=plan.action==rrzc_colocation::Action::kCreate;
  const auto generation=z->GetResetState();
  if(!generation.known || !generation.stable || generation.generation!=plan.generation ||
      z->IsFull() || z->capacity_<required || z->IsEmpty()!=fresh ||
      !rrzc_content::Claimable(z->rrzc_content_.load(),rrzc_content::kSst))
    return z->CheckRelease();
  bool active=false,opened=false;
  if(fresh) {
    active=GetActiveIOZoneTokenIfAvailable();
    if(!active)return z->CheckRelease();
  }
  if(!own_open) {
    std::lock_guard<std::mutex> guard(zone_resources_mtx_);
    if(open_io_zones_<open_limit){++open_io_zones_;opened=true;}
  }
  if((!own_open && !opened) || !coloc_pool_->Commit(plan)) {
    if(opened)PutOpenIOZoneToken();
    if(active)PutActiveIOZoneToken();
    return z->CheckRelease();
  }
  z->rrzc_content_=rrzc_content::kSst;
  if(fresh)z->lifetime_=lifetime;
  if(!own_open)coloc_migration_targets_.insert(z);
  Info(logger_,"协同池取得目标：阶段=%s，Zone=%lu，类别=%u，新Active=%d，自持Open=%d，原因=%s",
       own_open?"新文件":"必要迁移",z->start_,static_cast<unsigned>(plan.tag),fresh,!own_open,
       plan.reason.c_str());
  *output=z;return IOStatus::OK();
}
