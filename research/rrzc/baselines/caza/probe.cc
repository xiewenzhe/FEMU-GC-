#include "rocksdb/db.h"
#include "rocksdb/convenience.h"
#include "rocksdb/env.h"
#include "fs_zenfs.h"
#include "rrzc_caza_bridge.h"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>

using namespace ROCKSDB_NAMESPACE;
static void Check(const Status& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}
static void Require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
static std::string Key(unsigned i) {
  char key[17]; std::snprintf(key, sizeof(key), "%016u", i); return key;
}
static std::string Value(unsigned i, unsigned version) {
  std::string value(4096, char('a' + i % 26));
  const auto prefix = Key(i) + Key(version);
  value.replace(0, prefix.size(), prefix); return value;
}
static void DumpMapping(DB& db, ZenFS& fs, const std::string& path) {
  std::vector<LiveFileMetaData> live; db.GetLiveFilesMetaData(&live);
  ZenFSSnapshot snapshot; ZenFSSnapshotOptions want;
  want.zone_ = want.zone_file_ = true; fs.GetZenFSSnapshot(snapshot, want);
  std::ofstream output(path); output << "sst,level,zone,start,length\n";
  for (const auto& item : live) {
    uint64_t bytes = 0;
    const std::string filename = item.directory + "/" + item.relative_filename;
    for (const auto& extent : snapshot.extents_) if (extent.filename == filename) {
      output << item.file_number << ',' << item.level << ',' << extent.zone_start << ','
             << extent.start << ',' << extent.length << '\n';
      bytes += extent.length;
    }
    Require(bytes > 0 && bytes == item.size, "native live-SST extent/size mismatch or skipped file");
  }
  output.flush(); Require(output.good(), "native mapping output failed");
}
#include "rr_probe.h"
#include "pressure_probe.h"
#include "nospace_probe.h"
static void Exercise(Env* env, const std::string& name, bool verify_only,
                     ZenFS* zenfs = nullptr, const std::string& mapping_path = "",
                     bool direct = false, bool rr = false, ZonedBlockDevice* device = nullptr,
                     const std::string& output = "", Logger* logger = nullptr) {
  Options options;
  options.env = env;
  options.create_if_missing = !verify_only;
  options.error_if_exists = !verify_only;
  options.compression = kNoCompression;
  options.disable_auto_compactions = true;
  // A two-level fixture keeps actual compaction output at L1. With seven
  // dynamic levels, CompactRange can write L6 then relabel it to target_level=1.
  options.num_levels = 2;
  options.write_buffer_size = 16 << 20;
  options.target_file_size_base = 16 << 20;
  options.max_background_jobs = 1;
  options.max_open_files = -1;
  options.use_direct_reads = direct;
  options.use_direct_io_for_flush_and_compaction = direct;
  DB* raw = nullptr;
  Check(DB::Open(options, name, &raw));
  std::unique_ptr<DB> db(raw);
  if (!verify_only) {
    WriteOptions write; write.disableWAL = true;
    FlushOptions flush; flush.wait = true;
    for (unsigned batch = 0; batch < 4; ++batch) {
      for (unsigned i = 0; i < 512; ++i)
        Check(db->Put(write, Key(batch * 512 + i), Value(batch * 512 + i, 0)));
      Check(db->Flush(flush));
    }
    // Overlap an existing L0 range so manual compaction must rewrite SST data.
    for (unsigned i = 0; i < 512; ++i) Check(db->Put(write, Key(i), Value(i, 1)));
    Check(db->Flush(flush));
    CompactRangeOptions compact;
    compact.change_level = true; compact.target_level = 1;
    Check(db->CompactRange(compact, nullptr, nullptr));
    for (unsigned i = 0; i < 512; ++i) Check(db->Put(write, Key(i), Value(i, 2)));
    Check(db->Flush(flush));
    std::vector<LiveFileMetaData> live;
    db->GetLiveFilesMetaData(&live);
    bool level_zero = false, level_one = false;
    for (const auto& item : live) {
      level_zero |= item.level == 0; level_one |= item.level == 1;
    }
    Require(level_zero && level_one, "probe did not construct both L0 and L1");
  }
  const auto verify = [&](unsigned version) {
    ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
    for (unsigned i = 0; i < 2048; ++i) {
      std::string found;
      Check(db->Get(read, Key(i), &found));
      Require(found == Value(i, i < 512 ? version : 0), "latest-value mismatch");
    }
    Check(db->VerifyChecksum());
  };
  if (rr && !verify_only) {
    Require(zenfs && device, "RR requires the actual ZenFS device");
    verify(2);
    const char* test_case = std::getenv("CAZA_TEST_CASE");
    ExerciseRR(*db, *zenfs, *device, output, logger, test_case && std::string(test_case) == "fault");
    WriteOptions write; write.disableWAL = true;
    for (unsigned i = 0; i < 512; ++i) Check(db->Put(write, Key(i), Value(i, 3)));
    FlushOptions flush; flush.wait = true; Check(db->Flush(flush));
  }
  verify(rr ? 3 : 2);
  if (zenfs) DumpMapping(*db, *zenfs, mapping_path);
  Check(db->Close());
}
int main(int argc, char** argv) {
  try {
    Require(argc == 3, "usage: probe posix|device|verify result-directory");
    Require(rrzc_caza::Enabled(), "CAZA mode is required");
    const std::string mode = argv[1], output = argv[2];
    const char* selected = std::getenv("CAZA_TEST_CASE");
    const std::string test_case = selected ? selected : "basic";
    Require(test_case == "basic" || test_case == "direct" || test_case == "rr" ||
            test_case == "pressure" || test_case == "gc" || test_case == "nospace" ||
            test_case == "fault", "unknown test case");
    if (mode == "posix") {
      Require(test_case != "rr" && test_case != "gc" && test_case != "fault" && test_case != "nospace",
              "maintenance needs a real device");
      const std::string path = output + "/posix-" + test_case;
      if (test_case == "pressure") {
        ExercisePressure(Env::Default(), path, false, nullptr, nullptr, output, false, nullptr);
        ExercisePressure(Env::Default(), path, true, nullptr, nullptr, output, false, nullptr);
      } else {
        Exercise(Env::Default(), path, false, nullptr, "", test_case == "direct");
        Exercise(Env::Default(), path, true, nullptr, "", test_case == "direct");
      }
    } else {
      Require(mode == "device" || mode == "verify", "unknown probe mode");
      std::shared_ptr<Logger> logger;
      Check(Env::Default()->NewLogger(output + "/zenfs-" + mode + ".log", &logger));
      logger->SetInfoLogLevel(DEBUG_LEVEL);
      auto owned = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
      auto* device = owned.get();
      Check(owned->Open(false, true));
      auto fs = std::make_shared<ZenFS>(owned.release(), FileSystem::Default(), logger);
      Check(fs->Mount(false));
      auto env = NewCompositeEnv(fs);
      if (test_case == "nospace")
        ExerciseNoSpace(env.get(), *fs, *device, output, mode == "verify", logger.get());
      else if (test_case == "pressure" || test_case == "gc")
        ExercisePressure(env.get(), "/caza-probe", mode == "verify", fs.get(), device, output,
                         test_case == "gc", logger.get());
      else
        Exercise(env.get(), "/caza-probe", mode == "verify", fs.get(), output + "/" + mode + "-mapping.csv",
                 test_case == "direct", test_case == "rr" || test_case == "fault", device, output, logger.get());
    }
    std::cout << "CAZA " << mode << " latest values/checksums/reopen passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "CAZA probe failed: " << error.what() << '\n'; return 1;
  }
}
