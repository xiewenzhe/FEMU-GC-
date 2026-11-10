// Diagnostic workload only: natural reads reach the unchanged hardware threshold.
#pragma once
#include <array>
#include <chrono>
#include <cstring>
#include <dlfcn.h>
#include <endian.h>
#include <fcntl.h>
#include <iterator>
#include <linux/nvme_ioctl.h>
#include <set>
#include <sys/ioctl.h>
#include <thread>
#include <unistd.h>

static std::array<uint64_t, 9> RRStats() {
  std::array<uint64_t, 9> data{}; nvme_admin_cmd command{};
  command.opcode = 2; command.nsid = 1;
  command.addr = reinterpret_cast<uint64_t>(data.data()); command.data_len = 72;
  command.cdw10 = (17U << 16) | 0xc3;
  const int fd = open("/dev/nvme0n1", O_RDONLY | O_CLOEXEC);
  Require(fd >= 0, "RR stats open failed");
  const int result = ioctl(fd, NVME_IOCTL_ADMIN_CMD, &command); close(fd);
  Require(result == 0, "RR stats query failed");
  for (auto& value : data) value = le64toh(value);
  Require(data[0] == 1 && data[1] == 1 && data[2] == data[3] + data[4], "RR stats contract mismatch");
  return data;
}
static uint64_t SstZone(ZenFS& fs, const std::string& filename) {
  ZenFSSnapshot snapshot; ZenFSSnapshotOptions want;
  want.zone_ = want.zone_file_ = true; fs.GetZenFSSnapshot(snapshot, want);
  std::set<uint64_t> zones;
  for (const auto& ext : snapshot.extents_) if (ext.filename == filename) zones.insert(ext.zone_start);
  Require(zones.size() == 1, "RR fixture requires one confirmed SST zone");
  return *zones.begin();
}
static void ExerciseRR(DB& db, ZenFS& fs, ZonedBlockDevice& device, const std::string& output,
                       Logger* logger, bool fault = false) {
  // Wait only for existing diagnostic configuration; this is not timed business.
  const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(3);
  bool ready = false;
  Require(logger != nullptr, "RR diagnostic logger missing");
  while (std::chrono::steady_clock::now() < until) {
    logger->Flush();
    std::ifstream log(output + "/zenfs-device.log");
    const std::string all((std::istreambuf_iterator<char>(log)), std::istreambuf_iterator<char>());
    if (all.find("V1影子：已启用") != std::string::npos) { ready = true; break; }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  Require(ready, "copy/commit diagnostics not ready");
  std::vector<LiveFileMetaData> live; db.GetLiveFilesMetaData(&live);
  std::string filename;
  for (const auto& item : live) if (item.level == 1) {
    Require(filename.empty(), "RR fixture requires one L1 SST");
    filename = item.directory + "/" + item.relative_filename;
  }
  Require(!filename.empty(), "RR fixture has no L1 SST");
  const auto source = SstZone(fs, filename);
  auto* zone = device.GetIOZone(source); Require(zone != nullptr, "RR source missing");
  const auto before_host = zone->GetResetState();
  rrzc_v1::RecentReport before; Check(device.GetZoneRecentReads(&before));
  Require(before.threshold == 1024 && before.blocks_per_zone == 128 && before.zones.size() == 64,
          "RR geometry/threshold mismatch");
  const auto index = source / (128ULL << 20); Require(index < before.zones.size(), "RR source out of bounds");
  const auto before_stats = RRStats();
  DumpMapping(db, fs, output + "/before-rr-mapping.csv");
  FileOptions options; options.use_direct_reads = true;
  std::unique_ptr<FSRandomAccessFile> reader;
  Check(fs.NewRandomAccessFile(filename, options, &reader, nullptr));
  void* raw = nullptr; Require(posix_memalign(&raw, 4096, 4096) == 0, "RR read buffer allocation failed");
  std::unique_ptr<void, decltype(&free)> memory(raw, &free);
  using Counter = uint64_t (*)();
  Counter fault_fired = nullptr, fault_offset = nullptr;
  if (fault) {
    const auto arm = reinterpret_cast<void (*)()>(dlsym(RTLD_DEFAULT, "rrzc_fault_arm"));
    fault_fired = reinterpret_cast<Counter>(dlsym(RTLD_DEFAULT, "rrzc_fault_fired"));
    fault_offset = reinterpret_cast<Counter>(dlsym(RTLD_DEFAULT, "rrzc_fault_offset"));
    Require(arm && fault_fired && fault_offset, "test-only fault library missing"); arm();
  }
  bool observed_error = false;
  std::string page;
  unsigned issued = 0; uint64_t mapped = source;
  while (issued < 1100 && mapped == source) {
    Slice result;
    const auto status = reader->Read(0, 4096, IOOptions(), &result, static_cast<char*>(raw), nullptr);
    if (!status.ok()) {
      Require(fault && !observed_error && fault_fired() == 1, "unexpected RR read/migration error");
      observed_error = true;
      const auto failed_state = zone->GetResetState();
      Require(SstZone(fs, filename) == source && failed_state.known &&
              failed_state.generation == before_host.generation, "failed copy changed source mapping/generation");
      DumpMapping(db, fs, output + "/fault-mapping.csv");
      std::ofstream error(output + "/migration-error.txt"); error << status.ToString() << '\n';
      error.flush(); Require(error.good(), "original migration error output failed");
      // The next normal read retries the existing strict-RR path. No manual migration/reset call.
      continue;
    }
    Require(result.size() == 4096, "RR raw SST short read");
    if (page.empty()) page = result.ToString();
    else Require(result.ToString() == page, "RR changed SST bytes");
    ++issued;
    if (issued % 32 == 0) mapped = SstZone(fs, filename);
  }
  Require(mapped != source, "natural reads did not cause RR migration");
  if (fault) {
    Require(observed_error && fault_fired() == 1, "injected copy failure was not observed exactly once");
    std::ofstream report(output + "/fault-state.csv");
    report << "failures,failed_write_offset,source,target\n1," << fault_offset() << ',' << source << ',' << mapped << '\n';
    report.flush(); Require(report.good(), "fault state output failed");
  }
  const auto after_stats = RRStats();
  rrzc_v1::RecentReport after; Check(device.GetZoneRecentReads(&after));
  const auto after_host = zone->GetResetState();
  auto* target = device.GetIOZone(mapped); Require(target != nullptr, "RR target missing");
  const auto target_host = target->GetResetState();
  const auto target_index = mapped / (128ULL << 20);
  Require(target_host.known && target_index < after.zones.size(), "RR target generation unknown");
  Require(after_stats[6] == before_stats[6] + 1 && after_stats[8] <= 1024,
          "RR threshold-event/highest-RC mismatch");
  Require(before_host.known && after_host.known && after_host.generation == before_host.generation + 1 &&
          after.zones[index].generation == before.zones[index].generation + 1 &&
          after.zones[index].rc == 0 && after.zones[index].delta10 == 0 && after.zones[index].delta30 == 0 &&
          zone->IsEmpty() && zone->used_capacity_.load() == 0, "RR source reset/history mismatch");
  DumpMapping(db, fs, output + "/after-rr-mapping.csv");
  std::ofstream state(output + "/rr-state.csv");
  state << "stage,source,mapped,host_generation,device_generation,source_rc,threshold_events,highest_rc,issued,mapped_host_generation,mapped_device_generation\n";
  state << "before," << source << ',' << source << ',' << before_host.generation << ','
        << before.zones[index].generation << ',' << before.zones[index].rc << ','
        << before_stats[6] << ',' << before_stats[8] << ",0," << before_host.generation << ',' << before.zones[index].generation << '\n';
  state << "after," << source << ',' << mapped << ',' << after_host.generation << ','
        << after.zones[index].generation << ',' << after.zones[index].rc << ','
        << after_stats[6] << ',' << after_stats[8] << ',' << issued << ','
        << target_host.generation << ',' << after.zones[target_index].generation << '\n';
  state.flush(); Require(state.good(), "RR state output failed");
}
