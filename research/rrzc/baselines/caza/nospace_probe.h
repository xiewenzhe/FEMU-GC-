// Exhaust only allocator-eligible space; reserved/metadata Zones remain protected.
#pragma once
static void NoSpaceValues(DB& db, unsigned version, const Snapshot* snapshot = nullptr) {
  ReadOptions read; read.snapshot = snapshot; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < PressureKeys; ++k) {
    std::string found; Check(db.Get(read, Key(k), &found));
    Require(found == Value(k, version), "NoSpace committed-value mismatch");
  }
}
static void ExerciseNoSpace(Env* env, ZenFS& fs, ZonedBlockDevice& device,
                            const std::string& output, bool verify_only, Logger* logger) {
  Options options; options.env = env; options.num_levels = 2; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.write_buffer_size = 32 << 20;
  options.target_file_size_base = 8 << 20; options.max_background_jobs = 1; options.max_open_files = -1;
  options.level0_slowdown_writes_trigger = 240; options.level0_stop_writes_trigger = 256;
  unsigned durable_version = 0;
  if (!verify_only) ExercisePressure(env, "/caza-probe", false, &fs, &device, output, false, logger);
  else {
    std::ifstream expected(output + "/nospace-version.txt"); expected >> durable_version;
    Require(bool(expected) && durable_version > 0 && durable_version < 33, "invalid NoSpace expected version");
  }
  DB* raw = nullptr; Check(DB::Open(options, "/caza-probe", &raw)); std::unique_ptr<DB> db(raw);
  if (!verify_only) {
    constexpr uint64_t mib = 1ULL << 20;
    void* raw_buffer = nullptr; Require(posix_memalign(&raw_buffer, 4096, mib) == 0, "NoSpace buffer failed");
    std::unique_ptr<void, decltype(&free)> buffer(raw_buffer, &free);
    FileOptions file_options; file_options.use_direct_writes = true;
    std::ofstream fillers(output + "/nospace-fillers.csv"); fillers << "path,bytes,fill_byte,status\n";
    bool exhausted = false;
    for (unsigned i = 0; i < 64 && !exhausted; ++i) {
      const auto path = "/caza-probe/nospace-filler-" + std::to_string(i) + ".bin";
      Require(fs.FileExists(path, IOOptions(), nullptr).IsNotFound(), "refuse overwriting NoSpace filler");
      std::memset(raw_buffer, 64 + i, mib);
      std::unique_ptr<FSWritableFile> file; Check(fs.NewWritableFile(path, file_options, &file, nullptr));
      file->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
      uint64_t written = 0;
      for (; written < 128 * mib; written += mib) {
        const auto status = file->Append(Slice(static_cast<char*>(raw_buffer), mib), IOOptions(), nullptr);
        if (!status.ok()) {
          Require(status.IsNoSpace(), "unexpected filler failure"); exhausted = true;
          std::ofstream error(output + "/nospace-filler-error.txt"); error << status.ToString() << '\n';
          error.flush(); Require(error.good(), "NoSpace filler error output failed"); break;
        }
      }
      const auto closed = file->Close(IOOptions(), nullptr);
      Require(closed.ok() || (exhausted && closed.IsNoSpace()), "NoSpace filler close failed unexpectedly");
      fillers << path << ',' << written << ',' << 64 + i << ',' << (exhausted ? "NoSpace" : "OK") << '\n';
      std::cout << "NoSpace filler " << i << " bytes=" << written << " exhausted=" << exhausted << std::endl;
    }
    fillers.flush(); Require(fillers.good() && exhausted, "ordinary filler space did not exhaust");
    PressureZones(fs, device, output + "/nospace-filled-zones.csv");
    WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
    const Snapshot* committed = db->GetSnapshot(); Require(committed != nullptr, "NoSpace snapshot missing");
    bool failed = false;
    for (unsigned version = 1; version <= 32; ++version) {
      for (unsigned k = 0; k < PressureKeys; ++k) Check(db->Put(write, Key(k), Value(k, version)));
      const auto status = db->Flush(flush);
      if (!status.ok()) {
        Require(status.IsNoSpace(), "unexpected SST failure while exhausting eligible SST capacity");
        failed = true;
        std::ofstream error(output + "/nospace-sst-error.txt"); error << status.ToString() << '\n';
        error.flush(); Require(error.good(), "original SST NoSpace output failed"); break;
      }
      durable_version = version; db->ReleaseSnapshot(committed); committed = db->GetSnapshot();
      Require(committed != nullptr, "NoSpace committed snapshot missing");
    }
    Require(failed, "CAZA SST path did not return NoSpace");
    NoSpaceValues(*db, durable_version, committed); db->ReleaseSnapshot(committed);
    DumpMapping(*db, fs, output + "/nospace-failed-mapping.csv");
    const auto closed = db->Close(); Require(closed.ok() || closed.IsNoSpace(), "unexpected failed-DB close status");
    db.reset();
    // Reopening also needs an ordinary metadata target. Content isolation can
    // consume the first freed Zone for MANIFEST/CURRENT; release two real files
    // instead of bypassing that guard or forcing the SST into the metadata Zone.
    std::vector<std::pair<uint64_t, uint64_t>> released;
    for (unsigned index = 0; index < 2; ++index) {
      const auto file = "/caza-probe/nospace-filler-" + std::to_string(index) + ".bin";
      const auto start = SstZone(fs, file); const auto before = device.GetIOZone(start)->GetResetState();
      Require(before.known, "NoSpace recovery generation unknown"); released.push_back({start, before.generation});
      Check(fs.DeleteFile(file, IOOptions(), nullptr));
    }
    Require(released[0].first != released[1].first, "recovery filler files did not release two distinct Zones");
    std::cout << "NoSpace recovery: two filler files deleted for isolated metadata and SST capacity" << std::endl;
    Check(DB::Open(options, "/caza-probe", &raw)); db.reset(raw);
    NoSpaceValues(*db, durable_version);  // Failed, WAL-disabled memtable never became committed SST data.
    ++durable_version;
    for (unsigned k = 0; k < PressureKeys; ++k) Check(db->Put(write, Key(k), Value(k, durable_version)));
    Check(db->Flush(flush));
    std::ofstream resets(output + "/nospace-released-zones.csv"); resets << "zone,before,after\n";
    for (const auto& item : released) {
      const auto after = device.GetIOZone(item.first)->GetResetState();
      Require(after.known && after.generation == item.second + 1, "deleted filler was not safely reset for recovery");
      resets << item.first << ',' << item.second << ',' << after.generation << '\n';
    }
    resets.flush(); Require(resets.good(), "NoSpace released-zone output failed");
    std::ofstream expected(output + "/nospace-version.txt"); expected << durable_version << '\n';
    expected.flush(); Require(expected.good(), "NoSpace expected version output failed");
    std::ofstream state(output + "/nospace-state.csv");
    state << "filler_nospace,sst_nospace,reopened_committed_values,recovery_flush,reclaimed_zone,generation_before,generation_after,final_version,released_filler_files\n"
          << "1,1,1,1," << released.back().first << ',' << released.back().second << ','
          << released.back().second + 1 << ',' << durable_version << ",2\n";
    state.flush(); Require(state.good(), "NoSpace state output failed");
  }
  NoSpaceValues(*db, durable_version); Check(db->VerifyChecksum());
  DumpMapping(*db, fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
  Check(db->Close());
}
