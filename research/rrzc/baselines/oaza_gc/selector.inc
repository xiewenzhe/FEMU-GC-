if (rrzc_caza::OazaEnabled()) {
  migrate_zones_start.clear();
  try {
    std::string anchor;
    for (const auto& ext : snapshot.extents_) if (rrzc_caza::IsSst(ext.filename)) { anchor = ext.filename; break; }
    if (anchor.empty()) continue;
    const auto frame = rrzc_caza::Bridge().Capture(reinterpret_cast<uintptr_t>(zbd_), anchor);
    const auto files = rrzc_caza::ModelFiles(frame.files);
    uint64_t invalid = 0, allocated = 0;
    for (const auto& z : snapshot.zones_) if (z.wp != z.start) {
      if (z.used_capacity > z.max_capacity) throw std::invalid_argument("invalid allocated Zone capacity");
      allocated = rrzc_related::Add(allocated, z.max_capacity);
      // Unwritten tail is not invalid data. A finished Zone's WP may include
      // sacrificed padding; that padding is not distinguishable in this snapshot.
      const auto written = std::min(z.wp - z.start, z.max_capacity);
      if (written < z.used_capacity) throw std::invalid_argument("Zone usage exceeds its write pointer");
      invalid = rrzc_related::Add(invalid, written - z.used_capacity);
    }
    if (!allocated) continue;
    const auto quantity = rrzc_oaza::Quantity(invalid, allocated, snapshot.zones_.size());
    const auto before = zbd_->GetMigrationReserveObservation(), after = zbd_->GetMigrationReserveObservation();
    uint64_t largest_empty = 0, reserve_capacity = 0;
    for (const auto& z : snapshot.zones_) {
      if (z.wp == z.start && !z.used_capacity) largest_empty = std::max(largest_empty, z.capacity);
      if (after.present && z.start == after.zone) reserve_capacity = z.max_capacity;
    }
    std::vector<rrzc_oaza::Zone> eligible;
    std::map<uint64_t, uint64_t> recovered_labels;
    for (const auto& file : snapshot.zone_files_) for (const auto& ext : file.extents)
      recovered_labels[ext.zone_start] = std::max(recovered_labels[ext.zone_start], static_cast<uint64_t>(file.lifetime_hint));
    for (const auto& z : snapshot.zones_) {
      if (z.capacity || !z.used_capacity || z.used_capacity >= z.max_capacity) continue;
      auto* native = zbd_->GetIOZone(z.start);
      if (!native || native->IsBusy() || !diagnostic_before.count(z.start) || !diagnostic_after.count(z.start) ||
          !IsMaintenanceWindowStable(diagnostic_before.at(z.start), diagnostic_after.at(z.start), native->GetResetState()) ||
          native->wp_ != z.wp || native->used_capacity_.load() != z.used_capacity) continue;
      const uint64_t label = native->lifetime_ == Env::WLTH_NOT_SET ? recovered_labels[z.start] :
          static_cast<uint64_t>(native->lifetime_);
      if (label <= static_cast<uint64_t>(Env::WLTH_NONE)) continue;
      rrzc_oaza::Zone zone{z.start, z.used_capacity, z.max_capacity, label, {}};
      std::map<uint64_t, uint64_t> bytes;
      rrzc_guard::Bounds bounds;
      bool known = true;
      for (const auto& ext : snapshot.extents_) if (ext.zone_start == z.start) {
        if (!rrzc_caza::IsSst(ext.filename)) { known = false; break; }
        const auto number = rrzc_caza::Number(ext.filename);
        bytes[number] = rrzc_related::Add(bytes[number], ext.length);
        bounds.Add(ext.length, zbd_->GetBlockSize(), ZoneFile::SPARSE_HEADER_SIZE, z.max_capacity);
      }
      if (!known || !bounds.Complete(z.used_capacity) ||
          !(largest_empty >= bounds.upper || rrzc_target::Fits(z.start, z.used_capacity, true,
              bounds, reserve_capacity, before, after))) continue;
      for (const auto& part : bytes) {
        const auto file = std::find_if(files.begin(), files.end(), [&](const auto& f) { return f.number == part.first; });
        if (file == files.end() || !file->keys) { known = false; break; }
        const int reference = file->level == frame.cold_options.levels - 1 ? file->level - 1 : file->level + 1;
        zone.parts.push_back({part.first, part.second, rrzc_oaza::Ratio(*file, reference, files)});
      }
      if (!known) continue;
      const auto cost = rrzc_oaza::Cost(zone, frame.amplification, rrzc_caza::OazaVariant());
      Info(logger_, "OAZA-GC candidate: zone=%lu valid=%lu capacity=%lu label=%lu cost=%.12Lf variant=%s",
           z.start, z.used_capacity, z.max_capacity, zone.label, cost, rrzc_caza::OazaVariantName());
      eligible.push_back(std::move(zone));
    }
    const auto chosen = rrzc_oaza::Select(eligible, quantity, frame.amplification, rrzc_caza::OazaVariant());
    migrate_zones_start.insert(chosen.begin(), chosen.end());
    Info(logger_, "OAZA-GC frame: invalid=%lu allocated=%lu zones=%zu requested=%lu selected=%zu variant=%s threshold=0.15 avg_invalid=0.5 snapshot=%lu",
         invalid, allocated, snapshot.zones_.size(), quantity, chosen.size(), rrzc_caza::OazaVariantName(), frame.version);
  } catch (const std::exception& error) { Error(logger_, "OAZA-GC deferred: %s", error.what()); continue; }
}
