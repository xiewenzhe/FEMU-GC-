// Normal DB writes create several partially invalid full Zones. No chosen victim,
// direct Finish/Reset, injected RC, or altered OAZA quantity/cost is used here.
#pragma once
static void ExerciseOazaGC(Env* env, ZenFS& fs, ZonedBlockDevice& device,
                           const std::string& output, bool verify_only, Logger* logger, bool recovery = false) {
  constexpr unsigned groups = 12, keys_per_group = 6144, all_keys = groups * keys_per_group;
  Options options; options.env = env; options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.num_levels = 2;
  options.write_buffer_size = 16 << 20; options.target_file_size_base = 8 << 20;
  options.max_background_jobs = 1; options.max_open_files = -1;
  options.level0_slowdown_writes_trigger = 240; options.level0_stop_writes_trigger = 256;
  DB* raw = nullptr; Check(DB::Open(options, "/caza-probe", &raw)); std::unique_ptr<DB> db(raw);
  std::vector<unsigned> hot_begin(groups), versions(groups);
  std::vector<uint64_t> sources(groups), generations(groups);
  const auto state_path = output + "/oaza-gc-fixture.csv";
  WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
  CompactRangeOptions compact; compact.change_level = true; compact.target_level = 1;
  if (!verify_only) {
    for (unsigned g = 0; g < groups; ++g) {
      const unsigned base = g * keys_per_group;
      for (unsigned batch = 0; batch < 24; ++batch) {
        for (unsigned k = base + batch * 256; k < base + (batch + 1) * 256; ++k)
          Check(db->Put(write, Key(k), Value(k, 0)));
        Check(db->Flush(flush));
      }
      for (unsigned k = base; k < base + 256; ++k) Check(db->Put(write, Key(k), Value(k, 0)));
      Check(db->Flush(flush));
      const auto low_string = Key(base), high_string = Key(base + keys_per_group);
      const Slice low(low_string), high(high_string);
      Check(db->CompactRange(compact, &low, &high));
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
      std::sort(live.begin(), live.end(), [](const auto& a, const auto& b) { return a.smallestkey < b.smallestkey; });
      std::string retained;
      for (const auto& a : live) {
        if (a.level != 1 || a.smallestkey < Key(base) || a.largestkey >= Key(base + keys_per_group)) continue;
        const auto zone = SstZone(fs, a.directory + "/" + a.relative_filename);
        for (const auto& b : live) if (b.level == 1 && b.smallestkey > a.largestkey &&
            b.smallestkey >= Key(base) && b.largestkey < Key(base + keys_per_group) &&
            SstZone(fs, b.directory + "/" + b.relative_filename) == zone) {
          hot_begin[g] = std::stoul(a.smallestkey); sources[g] = zone;
          retained = b.directory + "/" + b.relative_filename; break;
        }
        if (!retained.empty()) break;
      }
      Require(!retained.empty(), "OAZA GC fixture found no co-resident L1 pair");
      auto* source = device.GetIOZone(sources[g]); Require(source != nullptr, "OAZA source missing");
      const auto reset = source->GetResetState(); Require(reset.known, "OAZA source generation unknown");
      generations[g] = reset.generation;
      // Whole-SST-fit allocation leaves small writable tails. Build more than
      // the unchanged ordinary Active quota, letting native FinishCheapest
      // close old tails when new groups need their own Zones.
      for (; versions[g] < 200 && source->capacity_ >= (2ULL << 20);) {
        ++versions[g];
        for (unsigned k = hot_begin[g]; k < hot_begin[g] + 256; ++k)
          Check(db->Put(write, Key(k), Value(k, versions[g])));
        Check(db->Flush(flush));
      }
      const auto first = Key(hot_begin[g]), last = Key(hot_begin[g] + 256);
      const Slice hot_low(first), hot_high(last);
      Check(db->CompactRange(compact, &hot_low, &hot_high));
      Require(source->used_capacity_.load() > 0 && source->used_capacity_.load() < source->max_capacity_ / 2,
              "OAZA source not partially invalid with retained SSTs");
      Require(SstZone(fs, retained) == sources[g], "retained OAZA SST moved before space pressure");
    }
    Require(std::set<uint64_t>(sources.begin(), sources.end()).size() == groups,
            "OAZA GC fixture requires distinct source Zones");
    const auto full_count = std::count_if(sources.begin(), sources.end(), [&](uint64_t start) {
      return device.GetIOZone(start)->IsFull();
    });
    Require(full_count >= 4, "ordinary Active pressure did not naturally finish enough source Zones");
    std::ofstream expected(state_path);
    expected << "group,hot_begin,version,source,generation\n";
    for (unsigned g = 0; g < groups; ++g)
      expected << g << ',' << hot_begin[g] << ',' << versions[g] << ',' << sources[g] << ',' << generations[g] << '\n';
    expected.flush(); Require(expected.good(), "OAZA fixture manifest failed");
    DumpMapping(*db, fs, output + "/oaza-before-gc.csv");
    PressureZones(fs, device, output + "/oaza-before-zones.csv");
    if (recovery) {
      // Preparation phase deliberately leaves GC pressure and validation reads
      // for the remount phase; no disabled maintenance threshold is substituted.
      DumpMapping(*db, fs, output + "/device-mapping.csv");
      Check(db->Close()); return;
    }
    auto* first_source = device.GetIOZone(sources[0]);
    FillForGC(fs, device, *first_source, output);
    const auto count_reset = [&] {
      unsigned count = 0;
      for (unsigned g = 0; g < groups; ++g) {
        const auto now = device.GetIOZone(sources[g])->GetResetState();
        if (now.known && now.generation > generations[g]) ++count;
      }
      return count;
    };
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(45);
    while (!count_reset() && std::chrono::steady_clock::now() < deadline) {
      logger->Flush(); std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    Require(count_reset() > 0, "OAZA dynamic quantity did not produce an actual reclaim");
    {
      // A first Reset is not completion of the multi-Zone batch. Wait for the
      // already-held native maintenance lock before demanding a complete map.
      auto stable = device.StrictLock();
      DumpMapping(*db, fs, output + "/oaza-after-gc.csv");
      PressureZones(fs, device, output + "/oaza-after-zones.csv");
      std::ofstream result(output + "/oaza-gc-reset.txt"); result << count_reset() << '\n';
      result.flush(); Require(result.good(), "OAZA reset evidence failed");
    }
  } else {
    std::ifstream expected(state_path); std::string line; std::getline(expected, line);
    for (unsigned g = 0; g < groups; ++g) {
      Require(bool(std::getline(expected, line)), "OAZA fixture row missing");
      std::replace(line.begin(), line.end(), ',', ' '); std::istringstream row(line); unsigned got;
      row >> got >> hot_begin[g] >> versions[g] >> sources[g] >> generations[g];
      Require(bool(row) && got == g && versions[g] && versions[g] <= 200 &&
              hot_begin[g] >= g * keys_per_group && hot_begin[g] + 256 <= (g + 1) * keys_per_group,
              "invalid OAZA expected data");
    }
  }
  if (recovery && verify_only) {
    // Host trace generations restart on mount. Measure this phase's baseline,
    // rather than compare its generation with another process's counter.
    for (unsigned g = 0; g < groups; ++g) generations[g] = device.GetIOZone(sources[g])->GetResetState().generation;
    std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
    Require(!live.empty(), "OAZA recovery has no committed SSTs");
    const auto frame = rrzc_caza::Bridge().Capture(reinterpret_cast<uintptr_t>(&device),
        live.front().directory + "/" + live.front().relative_filename);
    std::ofstream keys(output + "/oaza-recovered-keys.csv");
    keys << "sst,level,keys,smallest,largest,amplification\n";
    for (const auto& file : frame.files) {
      Require(file.keys > 0, "OAZA persisted key count was not recovered");
      keys << file.number << ',' << file.level << ',' << file.keys << ',' << file.smallest << ','
           << file.largest << ',' << frame.amplification << '\n';
    }
    keys.flush(); Require(keys.good(), "OAZA recovered keys output failed");
    ZenFSSnapshot snapshot; ZenFSSnapshotOptions want; want.zone_ = want.zone_file_ = true;
    fs.GetZenFSSnapshot(snapshot, want);
    std::map<uint64_t, uint64_t> labels;
    for (const auto& file : snapshot.zone_files_) for (const auto& ext : file.extents)
      labels[ext.zone_start] = std::max(labels[ext.zone_start], static_cast<uint64_t>(file.lifetime_hint));
    std::ofstream zones(output + "/oaza-recovered-zones.csv");
    zones << "zone,native_label,file_label,valid,capacity,remaining\n";
    bool restored = false;
    for (const auto& zone : snapshot.zones_) {
      auto* native = device.GetIOZone(zone.start); Require(native != nullptr, "recovered Zone missing");
      zones << zone.start << ',' << static_cast<uint64_t>(native->lifetime_) << ',' << labels[zone.start]
            << ',' << zone.used_capacity << ',' << zone.max_capacity << ',' << zone.capacity << '\n';
      restored |= !zone.capacity && zone.used_capacity && zone.used_capacity < zone.max_capacity &&
                  native->lifetime_ == Env::WLTH_NOT_SET && labels[zone.start] > Env::WLTH_NONE;
    }
    zones.flush(); Require(zones.good() && restored, "no unset Zone label with recovered file hints");
    auto* first_source = device.GetIOZone(sources[0]);
    FillForGC(fs, device, *first_source, output);
    const auto count_reset = [&] {
      unsigned count = 0;
      for (unsigned g = 0; g < groups; ++g) {
        const auto now = device.GetIOZone(sources[g])->GetResetState();
        if (now.known && now.generation > generations[g]) ++count;
      }
      return count;
    };
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(45);
    while (!count_reset() && std::chrono::steady_clock::now() < deadline) {
      logger->Flush(); std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    Require(count_reset() > 0, "OAZA dynamic quantity did not produce an actual reclaim");
    {
      // A first Reset is not completion of the multi-Zone batch. Wait for the
      // already-held native maintenance lock before demanding a complete map.
      auto stable = device.StrictLock();
      DumpMapping(*db, fs, output + "/oaza-after-gc.csv");
      PressureZones(fs, device, output + "/oaza-after-zones.csv");
      std::ofstream result(output + "/oaza-gc-reset.txt"); result << count_reset() << '\n';
      result.flush(); Require(result.good(), "OAZA reset evidence failed");
    }
  }
  // Validation-only boundary after workload completion, not a benchmark timing window.
  auto stable = device.StrictLock();
  ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < all_keys; ++k) {
    const unsigned g = k / keys_per_group;
    const auto version = k >= hot_begin[g] && k < hot_begin[g] + 256 ? versions[g] : 0;
    std::string found; Check(db->Get(read, Key(k), &found));
    Require(found == Value(k, version), "OAZA GC latest value mismatch");
  }
  Check(db->VerifyChecksum()); DumpMapping(*db, fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
  Check(db->Close());
}
