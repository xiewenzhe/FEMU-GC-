"""Generate a reviewable CAZA overlay from verified source; no build or device I/O."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[2]
SECOND = ROOT / 'research/rrzc/results/placement-ablation-20261105/阶段二'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def once(text, old, new):
    if text.count(old) != 1: raise ValueError('source anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


def change_function(text, signature, transform):
    start = text.index(signature)
    end = text.index('\n}', start) + 2
    return text[:start] + transform(text[start:end]) + text[end:]


def changes(files):
    bridge = '#include "rrzc_caza_bridge.h"\n'
    builder = 'table/block_based/block_based_table_builder.cc'
    text = bridge + files[builder]
    text = once(text, '  const InternalKeyComparator& internal_comparator;',
        '  int caza_level = -1;\n  std::string caza_smallest, caza_largest;\n'
        '  const InternalKeyComparator& internal_comparator;')
    text = once(text, '    props.column_family_id = tbo.column_family_id;',
        '    caza_level = tbo.level_at_creation;\n    props.column_family_id = tbo.column_family_id;')
    text = once(text, '  r->props.key_largest_seqno = std::max(r->props.key_largest_seqno, seq);',
        '''  if (rrzc_caza::Enabled()) {
    if (r->ts_sz != 0 || std::string(r->internal_comparator.user_comparator()->Name()) != "leveldb.BytewiseComparator" ||
        value_type == kTypeRangeDeletion) {
      r->SetStatus(Status::NotSupported("CAZA currently requires bytewise point keys without timestamps/range deletion"));
      return;
    }
    const std::string user = ExtractUserKey(ikey).ToString();
    if (r->caza_smallest.empty() || user < r->caza_smallest) r->caza_smallest = user;
    if (r->caza_largest.empty() || user > r->caza_largest) r->caza_largest = user;
  }
  r->props.key_largest_seqno = std::max(r->props.key_largest_seqno, seq);''')
    text = once(text, '  r->tail_size = r->offset - r->props.tail_start_offset;',
        '''  r->tail_size = r->offset - r->props.tail_start_offset;
  if (rrzc_caza::Enabled() && ok() && !r->caza_smallest.empty()) {
    try {
      rrzc_caza::Bridge().Ready(r->file->file_name(), r->caza_level,
                               r->caza_smallest, r->caza_largest);
    } catch (const std::exception& error) { r->SetStatus(Status::InvalidArgument(error.what())); }
  }''')
    files[builder] = text

    db = 'db/db_impl/db_impl_compaction_flush.cc'
    text = bridge + files[db]
    text = change_function(text, 'void DBImpl::InstallSuperVersionAndScheduleWork(', lambda part:
        once(part, '  EnqueuePendingCompaction(cfd);', '''  if (rrzc_caza::Enabled()) {
    std::vector<LiveFileMetaData> metadata;
    versions_->GetLiveFilesMetaData(&metadata);  // DB mutex already held; no filesystem callbacks.
    std::vector<rrzc_caza::Live> live;
    for (const auto& item : metadata) {
      rrzc_caza::File file;
      file.number = item.file_number; file.level = item.level;
      file.smallest = item.smallestkey; file.largest = item.largestkey;
      live.push_back({item.directory + "/" + item.relative_filename, item.column_family_name, std::move(file)});
    }
    rrzc_caza::Bridge().Publish(dbname_, std::move(live));
  }
  EnqueuePendingCompaction(cfd);'''))
    files[db] = text

    name = 'plugin/zenfs/fs/io_zenfs.h'
    text = bridge + files[name]
    text = once(text, '  void ObserveOwner();',
        '  IOStatus CazaBind();\n  IOStatus CazaAbort();\n  void ObserveOwner();')
    text = once(text, '  IOStatus BufferedWrite(const Slice& data);', '''  IOStatus CazaBuffer(const Slice& data, uint64_t offset);
  IOStatus CazaDrain();
  std::shared_ptr<rrzc_caza::Pending> caza_pending_;
  std::vector<std::string> caza_buffers_;
  uint64_t caza_bytes_ = 0, caza_truncate_ = 0;
  bool caza_drained_ = false, caza_truncate_known_ = false;
  IOStatus caza_error_;
  IOStatus BufferedWrite(const Slice& data);''')
    text = once(text, '    return zoneFile_->GetFileSize();',
        '    return caza_pending_ && !caza_drained_ ? wp : zoneFile_->GetFileSize();')
    files[name] = text

    name = 'plugin/zenfs/fs/io_zenfs.cc'
    text = bridge + files[name]
    text = once(text, '  open = true;\n}', '''  if (rrzc_caza::Enabled() && rrzc_caza::IsSst(zoneFile_->GetFilename())) {
    if (wp != 0) throw std::invalid_argument("CAZA refuses reopening an existing SST for writes");
    caza_pending_ = rrzc_caza::Bridge().Register(zoneFile_->GetFilename());
  }
  open = true;
}''')
    text = once(text, '  IOStatus s = CloseInternal();\n  if (buffered)',
        '  IOStatus s = CloseInternal();\n  if (caza_pending_) rrzc_caza::Bridge().Unregister(zoneFile_->GetFilename(), caza_pending_);\n  if (buffered)')
    text = change_function(text, 'IOStatus ZonedWritableFile::Truncate(', lambda part:
        once(part, '  zoneFile_->SetFileSize(size);', '''  if (caza_pending_ && !caza_drained_) {
    caza_truncate_ = size; caza_truncate_known_ = true;
    return IOStatus::OK();
  }
  zoneFile_->SetFileSize(size);'''))
    text = once(text, 'IOStatus ZonedWritableFile::DataSync() {',
        'IOStatus ZonedWritableFile::DataSync() {\n  const auto staged = CazaDrain();\n  if (!staged.ok()) return staged;')
    text = change_function(text, 'IOStatus ZonedWritableFile::CloseInternal() {', lambda part:
        once(part, '  IOStatus s = DataSync();', '''  // An abandoned TableBuilder never publishes Ready; discard only unwritten staging.
  rrzc_caza::Request ignored;
  if (caza_pending_ && !caza_drained_ && !rrzc_caza::Bridge().Read(caza_pending_, &ignored)) {
    caza_buffers_.clear();
    zoneFile_->SetFileSize(0);
    const auto abandoned = zoneFile_->CazaAbort();
    open = false;
    return abandoned;
  }
  IOStatus s = DataSync();'''))
    text = change_function(text, 'IOStatus ZonedWritableFile::CloseInternal() {', lambda part:
        once(part, '  IOStatus s = DataSync();\n  if (!s.ok()) return s;', '''  IOStatus s = DataSync();
  if (!s.ok()) {
    if (caza_pending_) {
      // Never replay a partial failure. Release native ownership even if metadata persistence fails.
      caza_buffers_.clear(); buffer_pos = 0;
      const auto cleanup = zoneFile_->CazaAbort();
      open = false;
      if (!cleanup.ok()) zoneFile_->GetZbd()->SetZoneDeferredStatus(cleanup);
    }
    return s;
  }'''))
    text = change_function(text, 'IOStatus ZonedWritableFile::CloseInternal() {', lambda part:
        once(part, '  s = zoneFile_->CloseWR();\n  if (!s.ok()) return s;', '''  s = zoneFile_->CloseWR();
  if (!s.ok()) {
    if (caza_pending_) {
      if (zoneFile_->IsOpenForWR()) {
        const auto cleanup = zoneFile_->CazaAbort();
        if (!cleanup.ok()) zoneFile_->GetZbd()->SetZoneDeferredStatus(cleanup);
      }
      open = false;
    }
    return s;
  }'''))
    for signature, offset in [('IOStatus ZonedWritableFile::Append(const Slice& data,', 'wp'),
                              ('IOStatus ZonedWritableFile::PositionedAppend(const Slice& data,', 'offset')]:
        text = change_function(text, signature, lambda part, offset=offset:
            once(part, '  IOStatus s;', '  if (caza_pending_) return CazaBuffer(data, ' + offset + ');\n  IOStatus s;'))
    text = change_function(text, 'IOStatus ZoneFile::CloseWR() {', lambda part:
        once(part, '  ReleaseWRLock();\n  return CloseActiveZone();', '''  const auto mapped = CazaBind();
  ReleaseWRLock();
  const auto closed = CloseActiveZone();
  return mapped.ok() ? closed : mapped;'''))
    text = once(text, 'MetadataWriter::~MetadataWriter() {}',
        '#include "rrzc_caza_writable.inc"\n\nMetadataWriter::~MetadataWriter() {}')
    files[name] = text

    name = 'plugin/zenfs/fs/fs_zenfs.cc'
    text = bridge + files[name]
    text = change_function(text, 'IOStatus ZenFS::SyncFileExtents(', lambda part:
        once(part, '  return IOStatus::OK();', '  return zoneFile->CazaBind();'))
    text = once(text, '  Info(logger_, "Superblock sequence %d", (int)superblock_->GetSeq());',
        '''  if (rrzc_caza::Enabled()) {
    std::lock_guard<std::mutex> lock(files_mtx_);
    for (const auto& item : files_) {
      const auto mapped = item.second->CazaBind();
      if (!mapped.ok()) return mapped;
    }
  }
  Info(logger_, "Superblock sequence %d", (int)superblock_->GetSeq());''')
    files[name] = text

    name = 'plugin/zenfs/fs/zbd_zenfs.h'
    text = bridge + files[name]
    text = once(text, '  uint64_t CompositionDevice() const {return composition_device_;}',
        '  IOStatus TryCazaIOZone(Env::WriteLifeTimeHint lifetime, uint8_t content, Zone** output);\n'
        '  uint64_t CompositionDevice() const {return composition_device_;}')
    files[name] = text
    name = 'plugin/zenfs/fs/zbd_zenfs.cc'
    text = bridge + files[name]
    text = once(text, '  WaitForOpenIOZoneToken(io_type == IOType::kWAL);', '''  WaitForOpenIOZoneToken(io_type == IOType::kWAL);
  if (io_type != IOType::kWAL && rrzc_caza::current) {
    const auto caza = TryCazaIOZone(file_lifetime, content, out_zone);
    if (!caza.ok()) { PutOpenIOZoneToken(); return caza; }
    if (*out_zone) return IOStatus::OK();
  }''')
    text = once(text, 'std::string ZonedBlockDevice::GetFilename()',
        '#include "rrzc_caza_zenfs.inc"\n\nstd::string ZonedBlockDevice::GetFilename()')
    files[name] = text
    return files


def prepare(source, output, trace_exit=False):
    source = Path(source).resolve(); output = Path(output).resolve()
    if output.exists(): raise ValueError('do not overwrite a source overlay')
    identity = json.loads((source / 'source_identity.json').read_text())
    proof = json.loads((source / 'export_complete.json').read_text())
    if digest(source / 'source.tar.gz') != proof['source_tar_sha256']:
        raise ValueError('source archive identity mismatch')
    files = {}
    with tarfile.open(source / 'source.tar.gz') as archive:
        for member in archive:
            if member.name == 'source_identity.json': continue
            if not member.isfile() or not member.name.startswith('rocksdb/'):
                raise ValueError('unsafe source archive member')
            relative = member.name[len('rocksdb/'):]
            if '..' in Path(relative).parts or relative not in identity['files'] or relative in files:
                raise ValueError('unregistered source path')
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != identity['files'][relative]:
                raise ValueError('source file identity mismatch')
            files[relative] = data
    if set(files) != set(identity['files']): raise ValueError('incomplete source archive')
    second = json.loads((SECOND / '副本身份.json').read_text())
    for name, expected in second['新源码'].items():
        path = SECOND / '新版源码' / name
        if digest(path) != expected: raise ValueError('frozen ZenFS overlay changed')
        files['plugin/zenfs/fs/' + name] = path.read_bytes()
    original = {name: data.decode() for name, data in files.items()
                if name in ('table/block_based/block_based_table_builder.cc',
                            'db/db_impl/db_impl_compaction_flush.cc') or
                name in ('plugin/zenfs/fs/' + unit for unit in
                         ('io_zenfs.h', 'io_zenfs.cc', 'fs_zenfs.cc', 'zbd_zenfs.h', 'zbd_zenfs.cc'))}
    edited = changes(dict(original))
    trace_proof = None
    if trace_exit:
        relative = 'research/rrzc/model/rrzc_placement_trace_exit.h'
        manifest = ROOT / 'research/rrzc/results/placement-ablation-20261105/阶段四/最终实现源码身份.json'
        expected = json.loads(manifest.read_text())[relative]
        header = ROOT / relative
        if digest(header) != expected:
            raise ValueError('stage-four trace exit fix identity mismatch')
        name = 'plugin/zenfs/fs/rrzc_placement_trace.h'
        original[name] = files[name].decode()
        edited[name] = header.read_text()
        # Verify that the exit-only replacement preserves the heat algorithm.
        marker = 'class HeatProjection'
        if original[name][original[name].index(marker):] != edited[name][edited[name].index(marker):]:
            raise ValueError('trace exit header changed the heat algorithm')
        name = 'plugin/zenfs/fs/zbd_zenfs.h'
        anchor = '  uint64_t composition_device_=rrzc_placement_trace::NewId();'
        edited[name] = once(edited[name], anchor,
            '  rrzc_placement_trace::DeviceLease composition_lease_;\n' + anchor)
        trace_proof = dict(header_sha256=expected, manifest_sha256=digest(manifest),
                           heat_algorithm_unchanged=True)
    for name, data in edited.items(): files[name] = data.encode()
    additions = {'include/rrzc_caza_policy.h': 'policy.h', 'include/rrzc_caza_bridge.h': 'bridge.h',
                 'plugin/zenfs/fs/rrzc_caza_zenfs.inc': 'zenfs_adapter.inc',
                 'plugin/zenfs/fs/rrzc_caza_writable.inc': 'writable_methods.inc'}
    for name, local in additions.items(): files[name] = (HERE / local).read_bytes()
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / 'src/rocksdb' / name; path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(data)
        if name in ('build_tools/build_detect_platform', 'build_tools/version.sh', 'plugin/zenfs/generate-version.sh'):
            path.chmod(0o755)
    patch = []
    for name, data in edited.items():
        patch.extend(difflib.unified_diff(original[name].splitlines(True), data.splitlines(True),
                      fromfile='a/' + name, tofile='b/' + name))
    for name in additions:
        patch.extend(difflib.unified_diff([], files[name].decode().splitlines(True),
                      fromfile='/dev/null', tofile='b/' + name))
    (output / 'caza.patch').write_text(''.join(patch))
    report = dict(source=str(source), source_archive_sha256=proof['source_tar_sha256'],
        original_library_sha256=identity['original_library_sha256'],
        zenfs_overlay_sha256=digest(SECOND / '副本身份.json'),
        trace_exit_fix=trace_proof,
        changed_units=list(edited), added_files=list(additions),
        source_sha256={name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        implementation_complete=False, compiled=False, device_verified=False,
        restrictions=['single DB default CF, fixed-width bytewise point keys, no timestamps/range tombstones',
                      '256MiB staged bytes per SST; extra memory and delayed writes must be measured',
                      'new overlay requires full affected-unit compilation; no original files changed'])
    (output / 'overlay_identity.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('Prepared CAZA overlay:', output, 'files:', len(files))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace-exit', action='store_true')
    args = parser.parse_args(); prepare(args.source, args.output, args.trace_exit)
