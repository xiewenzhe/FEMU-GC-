// Independent OAZA allocator. Caller owns the ordinary Open token.
IOStatus ZonedBlockDevice::TryCazaIOZone(Env::WriteLifeTimeHint lifetime,
                                       uint8_t content, Zone** output) {
  *output = nullptr;
  const auto* context = rrzc_caza::current;
  if (!rrzc_caza::Enabled() || !context) return IOStatus::OK();
  try {
    rrzc_related::File request;
    request.number = context->request.number; request.level = context->request.level;
    request.low = context->request.smallest; request.high = context->request.largest;
    request.keys = context->request.keys; request.bytes = context->request.bytes;
    if (request.bytes > GetZoneSize())
      return IOStatus::InvalidArgument("OAZA requires one complete physical SST to fit a Zone");
    const auto plan = rrzc_oaza::Allocate(request, rrzc_caza::ModelFiles(context->snapshot.files),
                                         context->snapshot.cold_options.levels - 1);
    const auto hint = [&](int level) { return static_cast<Env::WriteLifeTimeHint>(
        std::min(5, 3 + std::max(0, level - context->snapshot.cold_options.base_level))); };
    auto existing = [&](const rrzc_caza::ZoneId& identity, const char* stage) -> IOStatus {
      Zone* z = GetIOZone(identity.start);
      if (!z || !z->Acquire()) return IOStatus::OK();
      const auto reset = z->GetResetState();
      // Algorithm 2 requires space for the complete file, not just the next append.
      if (!reset.known || reset.generation != identity.generation || z->IsEmpty() ||
          z->capacity_ < request.bytes || (IsContentGuardEnabled() &&
          !rrzc_content::Compatible(z->rrzc_content_.load(), content))) return z->CheckRelease();
      if (IsContentGuardEnabled()) z->rrzc_content_.store(content);
      *output = z;
      Info(logger_, "OAZA target: stage=%s file=%lu level=%d zone=%lu ratio=%.12Lf snapshot=%lu",
           stage, request.number, request.level, z->start_, plan.ratio, context->snapshot.version);
      return IOStatus::OK();
    };
    auto lifetime_zone = [&](int level, bool top) -> IOStatus {
      for (auto* z : io_zones) {
        auto observed_hint = z->lifetime_;
        if (observed_hint == Env::WLTH_NOT_SET) {
          for (const auto& file : context->snapshot.files) for (const auto& identity : file.zones)
            if (identity.start == z->start_) observed_hint = std::max(observed_hint, hint(file.level));
        }
        if (!(top ? observed_hint == hint(0) || observed_hint == hint(1) : observed_hint == hint(level))) continue;
        const auto state = z->GetResetState();
        const auto status = existing({z->start_, state.generation}, top ? "top-lifetime" : "lifetime");
        if (!status.ok() || *output) return status;
      }
      return IOStatus::OK();
    };
    IOStatus status;
    if (plan.stage == rrzc_oaza::Placement::kTopLifetime) status = lifetime_zone(request.level, true);
    else if (plan.stage == rrzc_oaza::Placement::kBottomLifetime) status = lifetime_zone(request.level, false);
    else if (plan.stage == rrzc_oaza::Placement::kRatio) {
      for (auto number : plan.nearest_files) {
        const auto file = std::find_if(context->snapshot.files.begin(), context->snapshot.files.end(),
            [&](const auto& f) { return f.number == number; });
        for (const auto& z : file->zones) {
          status = existing(z, "ratio"); if (!status.ok() || *output) return status;
        }
      }
    }
    if (!status.ok() || *output) return status;
    if (plan.stage == rrzc_oaza::Placement::kFreshAndPromote) {
      status = ResetUnusedIOZones(); if (!status.ok()) return status;
      if (GetActiveIOZoneTokenIfAvailable()) {
        Zone* empty = nullptr; status = AllocateEmptyZone(&empty);
        if (!status.ok() || !empty) { PutActiveIOZoneToken(); if (!status.ok()) return status; }
        else {
          if (empty->capacity_ < request.bytes || (IsContentGuardEnabled() &&
              !rrzc_content::Claimable(empty->rrzc_content_.load(), content))) {
            const auto released = empty->CheckRelease(); PutActiveIOZoneToken();
            if (!released.ok()) return released;
          } else {
            empty->lifetime_ = hint(plan.associated_level);
            if (IsContentGuardEnabled()) empty->rrzc_content_.store(content);
            rrzc_caza::Bridge().Associate(request.number, plan.associated_level);
            *output = empty;
            Info(logger_, "OAZA target: stage=fresh-promote file=%lu from=%d associated=%d zone=%lu snapshot=%lu",
                 request.number, request.level, plan.associated_level, empty->start_, context->snapshot.version);
            return IOStatus::OK();
          }
        }
      }
    }
    // Algorithm 1 lines 10--12: lifetime-based original allocator fallback.
    Info(logger_, "OAZA fallback: file=%lu level=%d lifetime=%u snapshot=%lu",
         request.number, request.level, static_cast<unsigned>(lifetime), context->snapshot.version);
    return IOStatus::OK();
  } catch (const std::exception& error) { return IOStatus::InvalidArgument(error.what()); }
}
