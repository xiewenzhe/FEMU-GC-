// Functional fixtures use normal DB/FS writes and the unchanged resource limits.
#pragma once
#include <algorithm>
#include <sstream>

static constexpr unsigned PressureBatches = 24, PressureBatchKeys = 256;
static constexpr unsigned PressureKeys = PressureBatches * PressureBatchKeys;
static void PressureZones(ZenFS& fs, ZonedBlockDevice& device, const std::string& path) {
  ZenFSSnapshot snapshot; ZenFSSnapshotOptions want; want.zone_ = true;
  fs.GetZenFSSnapshot(snapshot, want);
  std::ofstream out(path);
  out << "zone,wp,remaining,used,capacity,generation,busy\n";
  for (const auto& z : snapshot.zones_) {
    auto* native = device.GetIOZone(z.start); Require(native != nullptr, "pressure zone missing");
    const auto reset = native->GetResetState(); Require(reset.known, "pressure generation unknown");
    out << z.start << ',' << z.wp << ',' << z.capacity << ',' << z.used_capacity << ','
        << z.max_capacity << ',' << reset.generation << ',' << native->IsBusy() << '\n';
  }
  out.flush(); Require(out.good(), "pressure zone output failed");
  device.LogZoneStats();
}

// Only the GC fixture uses non-SST filler files; their bytes never masquerade as SST data.
static void FillForGC(ZenFS& fs, ZonedBlockDevice& device, Zone& source,
                      const std::string& output) {
  constexpr uint64_t mib = 1ULL << 20;
  void* raw = nullptr; Require(posix_memalign(&raw, 4096, mib) == 0, "GC filler buffer failed");
  std::unique_ptr<void, decltype(&free)> buffer(raw, &free);
  FileOptions options; options.use_direct_writes = true;
  std::ofstream manifest(output + "/gc-fillers.csv"); manifest << "path,bytes,fill_byte\n";
  for (unsigned i = 0; i < 64 && !source.IsEmpty(); ++i) {
    const auto free_bytes = device.GetFreeSpace();
    const auto total = free_bytes + device.GetUsedSpace() + device.GetReclaimableSpace();
    // Merely entering the original 20% check does not qualify a partially
    // live Zone: the unchanged garbage threshold is 100-3*(20-free_percent).
    // At floor(10.5%) it is 70%; our retained genuine SSTs occupy <25%.
    const auto target = (total * 105 + 999) / 1000;
    if (free_bytes <= target + mib) break;
    const auto bytes = std::min(128 * mib, (free_bytes - target) / mib * mib);
    Require(bytes > 0, "GC filler made no progress");
    const auto path = "/caza-probe/gc-filler-" + std::to_string(i) + ".bin";
    Require(fs.FileExists(path, IOOptions(), nullptr).IsNotFound(), "refuse overwriting filler");
    std::memset(raw, 32 + i, mib);
    std::unique_ptr<FSWritableFile> file;
    Check(fs.NewWritableFile(path, options, &file, nullptr));
    file->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
    for (uint64_t done = 0; done < bytes; done += mib)
      Check(file->Append(Slice(static_cast<char*>(raw), mib), IOOptions(), nullptr));
    Check(file->Close(IOOptions(), nullptr));
    manifest << path << ',' << bytes << ',' << 32 + i << '\n';
    std::cout << "GC filler " << i << " bytes=" << bytes << " free=" << device.GetFreeSpace() << std::endl;
  }
  manifest.flush(); Require(manifest.good(), "GC filler manifest failed");
}

static void ExercisePressure(Env* env, const std::string& name, bool verify_only,
                             ZenFS* fs, ZonedBlockDevice* device,
                             const std::string& output, bool gc, Logger* logger) {
  Options options; options.env = env; options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.num_levels = 2;
  options.write_buffer_size = 16 << 20; options.target_file_size_base = 8 << 20;
  options.max_background_jobs = 1; options.max_open_files = -1;
  options.level0_slowdown_writes_trigger = 240; options.level0_stop_writes_trigger = 256;
  DB* raw = nullptr; Check(DB::Open(options, name, &raw)); std::unique_ptr<DB> db(raw);
  unsigned hot_begin = 0, hot_count = 0, version = 0;
  WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
  if (!verify_only) {
    for (unsigned batch = 0; batch < PressureBatches; ++batch) {
      for (unsigned k = batch * PressureBatchKeys; k < (batch + 1) * PressureBatchKeys; ++k)
        Check(db->Put(write, Key(k), Value(k, 0)));
      Check(db->Flush(flush));
      if (device) device->LogZoneStats();
    }
    // Overlapping L0 input prevents RocksDB's IsTrivialMove() from merely
    // relabelling disjoint files; the test needs a real L1 write through LIZA.
    for (unsigned k = 0; k < PressureBatchKeys; ++k) Check(db->Put(write, Key(k), Value(k, 0)));
    Check(db->Flush(flush));
    if (fs) {
      DumpMapping(*db, *fs, output + "/pressure-before-compaction.csv");
      PressureZones(*fs, *device, output + "/pressure-before-zones.csv");
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
      std::ofstream metadata(output + "/pressure-files.csv");
      metadata << "sst,level,smallest,largest\n";
      for (const auto& item : live) metadata << item.file_number << ',' << item.level << ','
                                           << item.smallestkey << ',' << item.largestkey << '\n';
      metadata.flush(); Require(metadata.good(), "pressure metadata output failed");
    }
    CompactRangeOptions compact; compact.change_level = true; compact.target_level = 1;
    Check(db->CompactRange(compact, nullptr, nullptr));
    if (fs) DumpMapping(*db, *fs, output + "/pressure-after-compaction.csv");
    if (logger) { Info(logger, "CAZA pressure baseline complete"); logger->Flush(); }
    if (gc) {
      Require(fs && device && logger, "GC needs a real isolated device");
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
      std::sort(live.begin(), live.end(), [](const auto& a, const auto& b) { return a.smallestkey < b.smallestkey; });
      // Pick a workload range whose normal placement shares a Zone with an unaffected L1 SST.
      // This chooses input keys, never the GC victim or migration destination.
      std::string retained; uint64_t source_start = 0;
      for (const auto& a : live) {
        if (a.level != 1) continue;
        const auto zone = SstZone(*fs, a.directory + "/" + a.relative_filename);
        for (const auto& b : live) if (b.level == 1 && b.file_number != a.file_number &&
            b.smallestkey > a.largestkey && SstZone(*fs, b.directory + "/" + b.relative_filename) == zone) {
          hot_begin = std::stoul(a.smallestkey); hot_count = PressureBatchKeys;
          Require(Key(hot_begin + hot_count - 1) <= a.largestkey, "GC hot range exceeds its L1 SST");
          source_start = zone; retained = b.directory + "/" + b.relative_filename; break;
        }
        if (!retained.empty()) break;
      }
      Require(!retained.empty(), "GC fixture found no co-resident genuine L1 SST pair");
      auto* source = device->GetIOZone(source_start); Require(source != nullptr, "GC source missing");
      const auto generation = source->GetResetState(); Require(generation.known, "GC generation unknown");
      for (; version < 160 && !source->IsFull(); ) {
        ++version;
        for (unsigned k = hot_begin; k < hot_begin + hot_count; ++k)
          Check(db->Put(write, Key(k), Value(k, version)));
        Check(db->Flush(flush));
      }
      Require(source->IsFull(), "GC fixture did not naturally fill the shared SST Zone");
      const auto low_key = Key(hot_begin), high_key = Key(hot_begin + hot_count);
      const Slice low(low_key), high(high_key);
      Check(db->CompactRange(compact, &low, &high));
      Require(source->IsFull() && source->used_capacity_.load() > 0 &&
              source->used_capacity_.load() < source->max_capacity_ / 4,
              "GC fixture needs retained live SST data below one quarter of its full Zone");
      Require(SstZone(*fs, retained) == source_start, "GC retained SST moved before space pressure");
      DumpMapping(*db, *fs, output + "/gc-before-mapping.csv");
      rrzc_v1::RecentReport recent_before; Check(device->GetZoneRecentReads(&recent_before));
      const auto source_index = source_start / (128ULL << 20);
      Require(recent_before.threshold == 1024 && recent_before.blocks_per_zone == 128 &&
              source_index < recent_before.zones.size(), "GC device contract mismatch");
      const auto stats_before = RRStats();
      FillForGC(*fs, *device, *source, output);
      const auto free_bytes = device->GetFreeSpace();
      const auto total = free_bytes + device->GetUsedSpace() + device->GetReclaimableSpace();
      Require(source->IsEmpty() || 100 * free_bytes / total == 10, "GC did not reach planned space pressure");
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(40);
      while (!source->IsEmpty() && std::chrono::steady_clock::now() < deadline) {
        logger->Flush(); std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      const auto after = source->GetResetState();
      Require(source->IsEmpty() && source->used_capacity_.load() == 0 && after.known &&
              after.generation == generation.generation + 1, "natural GC failed to reset the original source");
      const auto destination = SstZone(*fs, retained); Require(destination != source_start, "GC retained mapping unchanged");
      rrzc_v1::RecentReport recent_after; Check(device->GetZoneRecentReads(&recent_after));
      const auto stats_after = RRStats();
      Require(recent_after.zones[source_index].generation == recent_before.zones[source_index].generation + 1 &&
              recent_after.zones[source_index].rc == 0 && recent_after.zones[source_index].delta10 == 0 &&
              recent_after.zones[source_index].delta30 == 0 && stats_before[6] == stats_after[6],
              "GC reset counters/generation or unexpected RR event");
      DumpMapping(*db, *fs, output + "/gc-after-mapping.csv");
      std::ofstream state(output + "/gc-fixture.txt");
      state << hot_begin << ' ' << hot_count << ' ' << version << '\n';
      state.flush(); Require(state.good(), "GC expected-value fixture output failed");
      std::ofstream detail(output + "/gc-state.csv");
      detail << "source,target,generation_before,generation_after,free_after,total_after,hot_versions,device_generation_before,device_generation_after,source_rc_after,rr_events_before,rr_events_after\n"
             << source_start << ',' << destination << ',' << generation.generation << ',' << after.generation << ','
             << free_bytes << ',' << total << ',' << version << ',' << recent_before.zones[source_index].generation << ','
             << recent_after.zones[source_index].generation << ',' << recent_after.zones[source_index].rc << ','
             << stats_before[6] << ',' << stats_after[6] << '\n';
      detail.flush(); Require(detail.good(), "GC state output failed");
    }
  } else if (gc) {
    std::ifstream state(output + "/gc-fixture.txt"); state >> hot_begin >> hot_count >> version;
    Require(bool(state) && hot_count == PressureBatchKeys && version > 0 && version <= 160 &&
            hot_begin + hot_count <= PressureKeys, "invalid GC expected-value fixture");
  }
  ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < PressureKeys; ++k) {
    std::string found; Check(db->Get(read, Key(k), &found));
    Require(found == Value(k, k >= hot_begin && k < hot_begin + hot_count ? version : 0),
            "pressure latest-value mismatch");
  }
  Check(db->VerifyChecksum());
  if (fs) {
    DumpMapping(*db, *fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
    PressureZones(*fs, *device, output + (verify_only ? "/verify-zones.csv" : "/device-zones.csv"));
  }
  Check(db->Close());
}
