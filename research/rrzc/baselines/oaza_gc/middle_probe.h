// Exercise actual L2 writes, zero-overlap association into L3, then OR reuse.
#pragma once
static void ExerciseOazaMiddle(Env* env, ZenFS& fs, const std::string& output, bool verify_only) {
  Options options; options.env = env; options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only; options.compression = kNoCompression;
  options.disable_auto_compactions = true; options.num_levels = 4;
  options.level_compaction_dynamic_level_bytes = false;
  options.write_buffer_size = 16 << 20; options.target_file_size_base = 16 << 20;
  options.max_background_jobs = 1; options.max_open_files = -1;
  DB* raw = nullptr; Check(DB::Open(options, "/caza-probe", &raw)); std::unique_ptr<DB> db(raw);
  if (verify_only) {
    // Restore actual metadata from MANIFEST, then allocate another middle-level
    // output. Read-only reopen alone would not test OR inputs after recovery.
    DumpMapping(*db, fs, output + "/verify-before-write.csv");
    WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
    for (unsigned k = 0; k < 512; ++k) Check(db->Put(write, Key(k), Value(k, 4)));
    Check(db->Flush(flush));
    std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
    std::vector<std::string> inputs;
    for (const auto& f : live) if (f.level <= 2) inputs.push_back(f.relative_filename);
    CompactionOptions compact; compact.compression = kNoCompression; compact.output_file_size_limit = 16 << 20;
    Check(db->CompactFiles(compact, inputs, 2));
  } else {
    WriteOptions write; write.disableWAL = true; FlushOptions flush; flush.wait = true;
    for (unsigned batch = 0; batch < 4; ++batch) {
      for (unsigned k = batch * 512; k < (batch + 1) * 512; ++k) Check(db->Put(write, Key(k), Value(k, 0)));
      Check(db->Flush(flush));
    }
    const auto compact_to_l2 = [&] {
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
      std::vector<std::string> inputs;
      for (const auto& file : live) if (file.level == 0) inputs.push_back(file.relative_filename);
      Require(!inputs.empty(), "OAZA middle fixture has no L0 compaction input");
      CompactionOptions compact; compact.compression = kNoCompression; compact.output_file_size_limit = 16 << 20;
      Check(db->CompactFiles(compact, inputs, 2));
    };
    std::ofstream stages(output + "/oaza-middle-stages.csv"); stages << "stage,L2,L3\n";
    for (unsigned version = 1; version <= 3; ++version) {
      for (unsigned k = 0; k < 512; ++k) Check(db->Put(write, Key(k), Value(k, version)));
      Check(db->Flush(flush)); compact_to_l2();
      std::vector<LiveFileMetaData> live; db->GetLiveFilesMetaData(&live);
      unsigned l2 = 0, l3 = 0;
      for (const auto& file : live) { l2 += file.level == 2; l3 += file.level == 3; }
      Require(l3 > 0 && (version == 1 ? l2 == 0 : l2 > 0), "OAZA actual manifest level association incorrect");
      stages << version << ',' << l2 << ',' << l3 << '\n';
      DumpMapping(*db, fs, output + "/oaza-middle-stage-" + std::to_string(version) + ".csv");
    }
    stages.flush(); Require(stages.good(), "OAZA middle-stage evidence failed");
  }
  ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
  for (unsigned k = 0; k < 2048; ++k) {
    std::string found; Check(db->Get(read, Key(k), &found));
    Require(found == Value(k, k < 512 ? (verify_only ? 4 : 3) : 0), "OAZA middle latest value mismatch");
  }
  Check(db->VerifyChecksum());
  DumpMapping(*db, fs, output + (verify_only ? "/verify-mapping.csv" : "/device-mapping.csv"));
  Check(db->Close());
}
