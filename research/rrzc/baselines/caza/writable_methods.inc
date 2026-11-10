// No native SST write occurs until TableBuilder publishes its actual key range.
// The buffer is per writable handle and bounded: fail rather than silently use LIZA.
IOStatus ZonedWritableFile::CazaBuffer(const Slice& data, uint64_t offset) {
  std::lock_guard<std::mutex> lock(buffer_mtx_);
  if (caza_drained_ || !caza_error_.ok()) return IOStatus::IOError("CAZA append after drain/error");
  if (offset != wp) {
    caza_error_ = IOStatus::InvalidArgument("CAZA non-sequential append");
    return caza_error_;
  }
  constexpr uint64_t limit = 256ULL << 20;
  if (data.size() > limit || caza_bytes_ > limit - data.size()) {
    caza_error_ = IOStatus::NoSpace("CAZA per-SST memory staging limit is 256MiB");
    return caza_error_;
  }
  try { caza_buffers_.emplace_back(data.data(), data.size()); }
  catch (const std::exception& error) {
    caza_error_ = IOStatus::IOError(error.what());
    return caza_error_;
  }
  caza_bytes_ += data.size();
  wp += data.size();
  return IOStatus::OK();
}

IOStatus ZonedWritableFile::CazaDrain() {
  if (!caza_pending_) return IOStatus::OK();
  std::lock_guard<std::mutex> lock(buffer_mtx_);
  if (caza_drained_ || !caza_error_.ok()) return caza_error_;
  rrzc_caza::Request request;
  if (!rrzc_caza::Bridge().Read(caza_pending_, &request))
    return IOStatus::InvalidArgument("CAZA sync before final SST key metadata");
  try {
    rrzc_caza::Context context;
    context.request = request;
    context.snapshot = rrzc_caza::Bridge().Capture(
        reinterpret_cast<uintptr_t>(zoneFile_->GetZbd()), zoneFile_->GetFilename());
    rrzc_caza::Scope scope(context);
    // Reuse the original aligned buffered append, preserving sparse/non-sparse layout.
    // Original Append() also uses this mutex, so call BufferedWrite() directly here.
    const auto logical_wp = wp;
    wp = 0;
    for (const auto& bytes : caza_buffers_) {
      if (buffered) caza_error_ = BufferedWrite(Slice(bytes));
      else {
        void* aligned = nullptr;
        const int allocation = posix_memalign(&aligned, block_sz, bytes.size());
        if (allocation != 0) caza_error_ = IOStatus::IOError("CAZA aligned replay allocation failed");
        else {
          memcpy(aligned, bytes.data(), bytes.size());
          caza_error_ = zoneFile_->Append(aligned, bytes.size());
          free(aligned);
          if (caza_error_.ok()) wp += bytes.size();
        }
      }
      if (!caza_error_.ok()) break;
    }
    if (caza_error_.ok() && buffered) caza_error_ = FlushBuffer();
    if (caza_error_.ok() && caza_truncate_known_) {
      if (caza_truncate_ > zoneFile_->GetFileSize())
        caza_error_ = IOStatus::Corruption("CAZA truncate exceeds staged bytes");
      else zoneFile_->SetFileSize(caza_truncate_);
    }
    wp = logical_wp;
    if (caza_error_.ok()) {
      caza_drained_ = true;
      caza_buffers_.clear();
      caza_buffers_.shrink_to_fit();
    }
    return caza_error_;
  } catch (const std::exception& error) {
    caza_error_ = IOStatus::IOError(error.what());
    return caza_error_;
  }
}

IOStatus ZoneFile::CazaBind() {
  try {
  if (!rrzc_caza::Enabled() || linkfiles_.empty() || !rrzc_caza::IsSst(GetFilename()))
    return IOStatus::OK();
  if (linkfiles_.size() != 1) throw std::invalid_argument("CAZA SST hardlinks unsupported");
  std::vector<rrzc_caza::ZoneId> zones;
  for (auto* extent : extents_) {
    const auto reset = extent->zone_->GetResetState();
    if (!reset.known) throw std::invalid_argument("CAZA unknown extent generation");
    zones.push_back({extent->zone_->start_, reset.generation});
  }
  rrzc_caza::Bridge().Bind(reinterpret_cast<uintptr_t>(zbd_), GetFilename(), std::move(zones));
  return IOStatus::OK();
  } catch (const std::exception& error) {
    return IOStatus::InvalidArgument(error.what());
  }
}

IOStatus ZoneFile::CazaAbort() {
  // Preserve references to successfully appended bytes. The failed SST is never
  // installed in the LSM; its caller receives the original failure for cleanup.
  PushExtent();
  extent_start_ = NO_EXTENT;
  const auto persisted = PersistMetadata();
  ReleaseWRLock();
  const auto closed = CloseActiveZone();
  return persisted.ok() ? closed : persisted;
}
