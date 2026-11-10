// Called after acquiring the ordinary Open token, before the legacy allocator.
// A null output preserves that token and continues into the LIZA fallback.
IOStatus ZonedBlockDevice::TryCazaIOZone(Env::WriteLifeTimeHint lifetime,
                                       uint8_t content, Zone** output) {
  *output = nullptr;
  const auto* context = rrzc_caza::current;
  if (!rrzc_caza::Enabled() || !context) return IOStatus::OK();
  IOStatus status;
  auto existing = [&](const rrzc_caza::ZoneId& identity, const char* stage,
                      uint64_t count) -> IOStatus {
    Zone* zone = GetIOZone(identity.start);
    if (!zone || !zone->Acquire()) {
      Info(logger_, "CAZA candidate unavailable: stage=%s file=%lu zone=%lu generation=%lu reason=%s snapshot=%lu",
           stage, context->request.number, identity.start, identity.generation,
           zone ? "busy" : "missing", context->snapshot.version);
      return IOStatus::OK();
    }
    const auto reset = zone->GetResetState();
    const char* unavailable = !reset.known ? "unknown-generation" :
        reset.generation != identity.generation ? "generation" :
        zone->IsEmpty() ? "empty" : (zone->IsFull() || zone->capacity_ == 0) ? "full" :
        (IsContentGuardEnabled() && !rrzc_content::Compatible(zone->rrzc_content_.load(), content)) ? "content" : nullptr;
    if (unavailable) {
      Info(logger_, "CAZA candidate unavailable: stage=%s file=%lu zone=%lu generation=%lu reason=%s snapshot=%lu",
           stage, context->request.number, identity.start, identity.generation,
           unavailable, context->snapshot.version);
      return zone->CheckRelease();
    }
    if (IsContentGuardEnabled()) zone->rrzc_content_.store(content);
    *output = zone;
    Info(logger_, "CAZA target: stage=%s file=%lu level=%d zone=%lu generation=%lu overlap_ssts=%lu snapshot=%lu",
         stage, context->request.number, context->request.level, zone->start_,
         reset.generation, count, context->snapshot.version);
    return IOStatus::OK();
  };
  try {
    for (const auto& ranked : rrzc_caza::RankOverlap(context->request, context->snapshot.files)) {
      status = existing(ranked.zone, "overlap", ranked.overlapping_files);
      if (!status.ok() || *output) return status;
    }
    // This only resets zones whose native reference count is zero; no copying.
    status = ResetUnusedIOZones();
    if (!status.ok()) return status;
    if (GetActiveIOZoneTokenIfAvailable()) {
      Zone* empty = nullptr;
      status = AllocateEmptyZone(&empty);
      if (!status.ok() || !empty) {
        PutActiveIOZoneToken();
        if (!status.ok()) return status;
      } else {
        if (IsContentGuardEnabled() &&
            !rrzc_content::Claimable(empty->rrzc_content_.load(), content)) {
          const auto release = empty->CheckRelease();
          PutActiveIOZoneToken();
          return release.ok() ? IOStatus::Corruption("CAZA empty target content conflict") : release;
        }
        empty->lifetime_ = lifetime;
        if (IsContentGuardEnabled()) empty->rrzc_content_.store(content);
        *output = empty;
        Info(logger_, "CAZA target: stage=empty file=%lu level=%d zone=%lu snapshot=%lu",
             context->request.number, context->request.level, empty->start_, context->snapshot.version);
        return IOStatus::OK();
      }
    }
    for (const auto& zone : rrzc_caza::RankNearest(context->request, context->snapshot.files)) {
      status = existing(zone, "nearest", 0);
      if (!status.ok() || *output) return status;
    }
    Info(logger_, "CAZA fallback: stage=liza file=%lu level=%d snapshot=%lu",
         context->request.number, context->request.level, context->snapshot.version);
    return IOStatus::OK();
  } catch (const std::exception& error) {
    return IOStatus::InvalidArgument(error.what());
  }
}
