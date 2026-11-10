// Joint ColdMap check: normal writes produce several full, partially invalid Zones.
// The native selector computes the simulation and chooses the actual victim.
#pragma once
static void ExerciseColdMapJoint(Env* env, ZenFS& fs, ZonedBlockDevice& device,
                           const std::string& output, bool verify_only, Logger* logger) {
  constexpr unsigned groups = 3, keys_per_group = 6144, all_keys = groups * keys_per_group;
  Options options; options.env = env; options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.num_levels = 4;
  options.level_compaction_dynamic_level_bytes = false;
  options.max_bytes_for_level_base = 1 << 20; options.max_bytes_for_level_multiplier = 3;
  options.write_buffer_size = 16 << 20; options.target_file_size_base = 8 << 20;
  options.max_background_jobs = 1; options.max_open_files = -1;
  options.level0_slowdown_writes_trigger = 240; options.level0_stop_writes_trigger = 256;
  DB* raw = nullptr; Check(DB::Open(options, "/caza-probe", &raw)); std::unique_ptr<DB> db(raw);
  std::vector<unsigned> hot_begin(groups), versions(groups);
  std::vector<uint64_t> sources(groups), generations(groups);
  const auto state_path = output + "/joint-gc-fixture.csv";
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
      Require(!retained.empty(), "ColdMap GC fixture found no co-resident L1 pair");
      auto* source = device.GetIOZone(sources[g]); Require(source != nullptr, "ColdMap source missing");
      const auto reset = source->GetResetState(); Require(reset.known, "ColdMap source generation unknown");
      generations[g] = reset.generation;
      // Normal overlapping Flush writes fill this co-resident Zone; no direct
      // Finish or chosen victim is injected.
      for (; versions[g] < 200 && !source->IsFull();) {
        ++versions[g];
        for (unsigned k = hot_begin[g]; k < hot_begin[g] + 256; ++k)
          Check(db->Put(write, Key(k), Value(k, versions[g])));
        Check(db->Flush(flush));
      }
      const auto first = Key(hot_begin[g]), last = Key(hot_begin[g] + 256);
      const Slice hot_low(first), hot_high(last);
      Check(db->CompactRange(compact, &hot_low, &hot_high));
      Require(source->used_capacity_.load() > 0 && source->used_capacity_.load() < source->max_capacity_ / 2,
              "ColdMap source not partially invalid with retained SSTs");
      Require(SstZone(fs, retained) == sources[g], "retained ColdMap SST moved before space pressure");
    }
    Require(std::set<uint64_t>(sources.begin(), sources.end()).size() == groups,
            "ColdMap GC fixture requires distinct source Zones");
    const auto full_count = std::count_if(sources.begin(), sources.end(), [&](uint64_t start) {
      return device.GetIOZone(start)->IsFull();
    });
    Require(full_count == groups, "ordinary Active pressure did not naturally finish enough source Zones");
    std::ofstream expected(state_path);
    expected << "group,hot_begin,version,source,generation\n";
    for (unsigned g = 0; g < groups; ++g)
      expected << g << ',' << hot_begin[g] << ',' << versions[g] << ',' << sources[g] << ',' << generations[g] << '\n';
    expected.flush(); Require(expected.good(), "ColdMap fixture manifest failed");
    DumpMapping(*db, fs, output + "/joint-before-gc.csv");
    PressureZones(fs, device, output + "/joint-before-zones.csv");
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
    Require(count_reset() > 0, "ColdMap selector did not produce an actual reclaim");
    {
      // A first Reset is not completion of the multi-Zone batch. Wait for the
      // already-held native maintenance lock before demanding a complete map.
      auto stable = device.StrictLock();
      DumpMapping(*db, fs, output + "/joint-after-gc.csv");
      PressureZones(fs, device, output + "/joint-after-zones.csv");
      std::ofstream result(output + "/joint-gc-reset.txt"); result << count_reset() << '\n';
      result.flush(); Require(result.good(), "ColdMap reset evidence failed");
    }
  } else {
    std::ifstream expected(state_path); std::string line; std::getline(expected, line);
    for (unsigned g = 0; g < groups; ++g) {
      Require(bool(std::getline(expected, line)), "ColdMap fixture row missing");
      std::replace(line.begin(), line.end(), ',', ' '); std::istringstream row(line); unsigned got;
      row >> got >> hot_begin[g] >> versions[g] >> sources[g] >> generations[g];
      Require(bool(row) && got == g && versions[g] && versions[g] <= 200 &&
              hot_begin[g] >= g * keys_per_group && hot_begin[g] + 256 <= (g + 1) * keys_per_group,
              "invalid ColdMap expected data");
    }
  }
  // Validation-only boundary after workload completion, not a benchmark timing window.
  auto stable = device.StrictLock();
  ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < all_keys; ++k) {
    const unsigned g = k / keys_per_group;
    const auto version = k >= hot_begin[g] && k < hot_begin[g] + 256 ? versions[g] : 0;
    std::string found; Check(db->Get(read, Key(k), &found));
    Require(found == Value(k, version), "ColdMap GC latest value mismatch");
  }
  Check(db->VerifyChecksum()); DumpMapping(*db, fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
  Check(db->Close());
}
