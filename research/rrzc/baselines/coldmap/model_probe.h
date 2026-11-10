// Build a real four-level snapshot using public DB operations, then run exactly
// the selector's metadata simulator. This is not an automatic-GC benefit test.
#pragma once
static void ExerciseColdMapModel(Env* env, ZenFS& fs, ZonedBlockDevice& device,
                                 const std::string& output, bool verify_only) {
  Options options; options.env = env; options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.num_levels = 4;
  options.level_compaction_dynamic_level_bytes = false;
  options.max_bytes_for_level_base = 1 << 20; options.max_bytes_for_level_multiplier = 3;
  options.write_buffer_size = 16 << 20; options.target_file_size_base = 8 << 20;
  options.max_background_jobs = 1; options.max_open_files = -1;
  DB* raw = nullptr; Check(DB::Open(options, "/caza-probe", &raw)); std::unique_ptr<DB> db(raw);
  if (!verify_only) {
    WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
    for (unsigned batch = 0; batch < 4; ++batch) {
      for (unsigned k = batch * 512; k < (batch + 1) * 512; ++k) Check(db->Put(write, Key(k), Value(k, 0)));
      Check(db->Flush(flush));
    }
    const auto compact_l0 = [&](int level) {
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live); std::vector<std::string> inputs;
      for (const auto& f : live) if (f.level == 0) inputs.push_back(f.relative_filename);
      Require(!inputs.empty(), "ColdMap model has no L0 compaction inputs");
      CompactionOptions compact; compact.compression = kNoCompression; compact.output_file_size_limit = 8 << 20;
      Check(db->CompactFiles(compact, inputs, level));
    };
    compact_l0(2);
    // A real additional compaction provides a third observed event. An older
    // small L2 file can legitimately precede a generated file in the simulation.
    for (unsigned k = 0; k < 256; ++k) Check(db->Put(write, Key(k), Value(k, 0)));
    Check(db->Flush(flush)); compact_l0(2);
    for (unsigned batch = 0; batch < 2; ++batch) {
      for (unsigned k = batch * 512; k < (batch + 1) * 512; ++k) Check(db->Put(write, Key(k), Value(k, 1)));
      Check(db->Flush(flush));
    }
    for (unsigned k = 0; k < 256; ++k) Check(db->Put(write, Key(k), Value(k, 2)));
    Check(db->Flush(flush)); compact_l0(1);
    std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live); Require(!live.empty(), "no real model SSTs");
    const auto frame = rrzc_caza::Bridge().Capture(reinterpret_cast<uintptr_t>(&device),
        live.front().directory + "/" + live.front().relative_filename);
    Require(frame.compactions == 3, "native CompactFiles completion counter mismatch");
    std::vector<rrzc_related::File> files;
    for (const auto& f : frame.files) {
      rrzc_related::File item; item.number = f.number; item.level = f.level; item.low = f.smallest;
      item.high = f.largest; item.bytes = f.bytes; item.epoch = f.epoch; item.compacting = f.compacting;
      files.push_back(std::move(item));
    }
    rrzc_coldmap::AdaptiveDepth history; const auto depth = history.AtCleaning(frame.compactions);
    const auto result = rrzc_coldmap::Simulate(files, frame.cold_options, depth, rrzc_caza::ColdMapDirection());
    uint64_t largest_original = 0, created = 0, generated_pivots = 0;
    for (const auto& f : files) largest_original = std::max(largest_original, f.number);
    for (const auto& step : result.steps) { created += step.created.size(); generated_pivots += step.pivot > largest_original; }
    std::ofstream log(output + "/coldmap-model.csv");
    log << "compactions,depth,steps,created,generated_pivots,direction\n"
        << frame.compactions << ',' << depth << ',' << result.steps.size() << ',' << created << ','
        << generated_pivots << ',' << rrzc_caza::ColdMapDirectionName() << '\n';
    log.flush(); Require(log.good(), "ColdMap model proof output failed");
    std::ofstream details(output + "/coldmap-predicted-files.csv");
    details << "number,level,bytes,low_hex,high_hex\n";
    const auto hex = [](const std::string& value) {
      const char* chars = "0123456789abcdef"; std::string s;
      for (unsigned char c : value) { s += chars[c >> 4]; s += chars[c & 15]; } return s;
    };
    for (const auto& f : result.predicted_files) details << f.number << ',' << f.level << ',' << f.bytes << ','
        << hex(f.low) << ',' << hex(f.high) << '\n';
    details.flush(); Require(details.good(), "ColdMap predicted metadata output failed");
    std::ofstream input(output + "/coldmap-input-files.csv");
    input << "number,level,bytes,low_hex,high_hex\n";
    for (const auto& f : files) input << f.number << ',' << f.level << ',' << f.bytes << ',' << hex(f.low) << ',' << hex(f.high) << '\n';
    input.flush(); Require(input.good(), "ColdMap input snapshot output failed");
    std::ofstream steps(output + "/coldmap-model-steps.csv");
    steps << "pivot,source,destination,input_bytes,output_bytes,created,trivial\n";
    for (const auto& step : result.steps) steps << step.pivot << ',' << step.source << ',' << step.destination << ','
        << step.input_bytes << ',' << step.output_bytes << ',' << step.created.size() << ',' << step.trivial << '\n';
    steps.flush(); Require(steps.good(), "ColdMap simulation detail output failed");
    Require(depth == 3 && result.steps.size() == 3 && created > 0 && generated_pivots > 0,
            "real ColdMap snapshot did not simulate generated SSTs across three steps");
  }
  ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < 2048; ++k) {
    std::string found; Check(db->Get(read, Key(k), &found));
    Require(found == Value(k, k < 256 ? 2 : k < 1024 ? 1 : 0), "ColdMap model latest value mismatch");
  }
  Check(db->VerifyChecksum());
  DumpMapping(*db, fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
  Check(db->Close());
}
