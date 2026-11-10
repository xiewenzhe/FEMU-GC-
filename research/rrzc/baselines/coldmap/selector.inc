// Included in ZenFS::GCWorker after its native snapshot and candidate scan.
// The external method uses full, partially invalid Zones; it does not inherit
// the original model's invalid-percentage filter, scores, or reclaim budget.
if (rrzc_caza::ColdMapEnabled()) {
  migrate_zones_start.clear();
  try {
    std::string anchor;
    for (const auto& ext : snapshot.extents_)
      if (rrzc_caza::IsSst(ext.filename)) { anchor = ext.filename; break; }
    if (anchor.empty()) continue;
    const auto frame = rrzc_caza::Bridge().Capture(reinterpret_cast<uintptr_t>(zbd_), anchor);
    std::vector<rrzc_related::File> files;
    for (const auto& f : frame.files) {
      rrzc_related::File value;
      value.number = f.number; value.level = f.level; value.low = f.smallest;
      value.high = f.largest; value.bytes = f.bytes; value.epoch = f.epoch; value.compacting = f.compacting;
      files.push_back(std::move(value));
    }
    const auto depth = coldmap_depth.AtCleaning(frame.compactions);
    const auto model = rrzc_coldmap::Simulate(files, frame.cold_options, depth,
        rrzc_caza::ColdMapDirection());
    const auto reserve_before = zbd_->GetMigrationReserveObservation();
    const auto reserve_after = zbd_->GetMigrationReserveObservation();
    uint64_t largest_empty = 0, reserve_capacity = 0;
    for (const auto& z : snapshot.zones_) {
      if (z.wp == z.start && z.used_capacity == 0) largest_empty = std::max(largest_empty, z.capacity);
      if (reserve_after.present && z.start == reserve_after.zone) reserve_capacity = z.max_capacity;
    }
    std::vector<rrzc_coldmap::Zone> eligible;
    for (const auto& z : snapshot.zones_) {
      if (z.capacity || !z.used_capacity || z.used_capacity >= z.max_capacity) continue;
      auto* native = zbd_->GetIOZone(z.start);
      if (!native || native->IsBusy() || !diagnostic_before.count(z.start) || !diagnostic_after.count(z.start) ||
          !IsMaintenanceWindowStable(diagnostic_before.at(z.start), diagnostic_after.at(z.start), native->GetResetState()) ||
          native->wp_ != z.wp || native->used_capacity_.load() != z.used_capacity) continue;
      rrzc_coldmap::Zone zone{z.start, z.used_capacity, z.max_capacity, {}};
      rrzc_guard::Bounds bounds;
      bool known = true;
      for (const auto& ext : snapshot.extents_) if (ext.zone_start == z.start) {
        if (!rrzc_caza::IsSst(ext.filename)) { known = false; break; }
        const auto number = rrzc_caza::Number(ext.filename);
        if (!model.cold.count(number)) { known = false; break; }
        zone.parts.push_back({number, ext.length});
        bounds.Add(ext.length, zbd_->GetBlockSize(), ZoneFile::SPARSE_HEADER_SIZE, z.max_capacity);
      }
      if (!known || !bounds.Complete(z.used_capacity)) continue;
      const bool target = largest_empty >= bounds.upper || rrzc_target::Fits(z.start, z.used_capacity, true,
          bounds, reserve_capacity, reserve_before, reserve_after);
      if (!target) continue;
      const auto cold = rrzc_coldmap::ZoneCold(zone, model.cold);
      const auto score = rrzc_coldmap::Score(zone, model.cold);
      Info(logger_, "ColdMap candidate: zone=%lu valid=%lu capacity=%lu cold=%.12Lf score=%.12Lf snapshot=%lu",
           z.start, z.used_capacity, z.max_capacity, cold, score, frame.version);
      eligible.push_back(std::move(zone));
    }
    Info(logger_, "ColdMap frame: snapshot=%lu compactions=%lu depth=%lu simulated=%zu candidates=%zu byte_weighted=1 alpha=0.5 K=3",
         frame.version, frame.compactions, depth, model.steps.size(), eligible.size());
    for (const auto& step : model.steps)
      Info(logger_, "ColdMap prediction: pivot=%lu from=%d to=%d input=%lu output=%lu removed=%zu created=%zu trivial=%d intra_l0=%d",
           step.pivot, step.source, step.destination, step.input_bytes, step.output_bytes,
           step.removed.size(), step.created.size(), step.trivial, step.intra_l0);
    if (!eligible.empty()) {
      const auto selected = rrzc_coldmap::Select(eligible, model.cold);
      migrate_zones_start.insert(selected);
      Info(logger_, "ColdMap selected: zone=%lu direction=%s", selected, rrzc_caza::ColdMapDirectionName());
    }
  } catch (const std::exception& error) {
    // Missing/inconsistent metadata is not permission to silently run Greedy.
    Error(logger_, "ColdMap deferred: %s", error.what());
    continue;
  }
}
