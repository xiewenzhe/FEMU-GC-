"""One frozen workload client for every related-work arm; no strategy edits."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile

from run_native_host import BASE, COMMAND_ROOT, build_helper, payload_for

METHODS = {'caza': 'caza-v6-20261107', 'oaza_gc': 'oaza-gc-v7-20261109',
           'coldmap': 'coldmap-v5-20261109'}
ARMS = (('g2', 'caza', None), ('caza', 'caza', 'native'),
        ('oaza-literal', 'oaza_gc', 'literal'), ('oaza-adapted', 'oaza_gc', 'adapted'),
        ('cold-printed', 'coldmap', 'printed'), ('cold-survival', 'coldmap', 'artifact-survival'))


def once(text, old, new):
    assert text.count(old) == 1, 'shared client anchor changed: ' + old[:80]
    return text.replace(old, new)


def client_source():
    text = (COMMAND_ROOT / '校准-db_bench_tool.cc').read_text()
    text = '#include "util/crc32c.h"\n' + text
    text = once(text, '    Status s = Env::CreateFromUri(config_options, FLAGS_env_uri, FLAGS_fs_uri,\n'
                     '                                  &FLAGS_env, &env_guard);', r'''
    Status s;
    if (const char* path = std::getenv("RRZC_RELATED_ZENFS_LOG")) {
      if (FLAGS_fs_uri != "zenfs://dev:nvme0n1" || !FLAGS_env_uri.empty())
        throw std::runtime_error("explicit related logger requires the verified namespace");
      std::shared_ptr<Logger> logger;
      s = Env::Default()->NewLogger(path, &logger);
      if (s.ok()) {
        logger->SetInfoLogLevel(DEBUG_LEVEL);
        auto device = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
        auto opened = device->Open(false, true);
        if (!opened.ok()) s = Status::IOError(opened.ToString());
        else {
          auto fs = std::make_shared<ZenFS>(device.release(), FileSystem::Default(), logger);
          s = fs->Mount(false);
          if (s.ok()) { env_guard = NewCompositeEnv(fs); FLAGS_env = env_guard.get(); }
        }
      }
    } else {
      s = Env::CreateFromUri(config_options, FLAGS_env_uri, FLAGS_fs_uri, &FLAGS_env, &env_guard);
    }''')
    text = once(text, '        method = &Benchmark::RRZCSstLifetime;',
                '        method = &Benchmark::RRZCSstLifetime;\n'
                '      } else if (name == "rrzcrelateddigest") {\n'
                '        method = &Benchmark::RRZCRelatedDigest;')
    text = once(text, '  void RRZCSstLifetime(ThreadState* thread) {', r'''
  // Logical contents only. This verification scan is outside the timed workload.
  void RRZCRelatedDigest(ThreadState*) {
    ReadOptions read; read.fill_cache = false; read.verify_checksums = true;
    std::unique_ptr<Iterator> it(db_.db->NewIterator(read));
    uint32_t crc = 0; uint64_t count = 0, bytes = 0;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
      if (it->key().size() != 16 || it->value().size() != 4096)
        throw std::runtime_error("unexpected logical key/value size");
      crc = crc32c::Extend(crc, it->key().data(), it->key().size());
      crc = crc32c::Extend(crc, it->value().data(), it->value().size());
      ++count; bytes += it->key().size() + it->value().size();
    }
    if (!it->status().ok() || count != uint64_t(FLAGS_num))
      throw std::runtime_error("logical digest count/status mismatch");
    fprintf(stderr, "Related logical digest: keys=%llu bytes=%llu crc32c=%u\n",
        (unsigned long long)count, (unsigned long long)bytes, crc);
  }
  void RRZCSstLifetime(ThreadState* thread) {''')
    # Preserve every generated value and the Put order; add a generated-input checksum.
    start = text.index('  void RRZCCalibratedLoad(')
    end = text.index('  // 序号对应', start)
    body = text[start:end]
    body = once(body, '    uint64_t hash=1469598103934665603ULL;',
                '    uint64_t hash=1469598103934665603ULL; uint32_t input_crc=0;')
    body = once(body, 'auto status=db_.db->Put(write_options_,key,generator.Generate());',
                'const auto payload=generator.Generate();\n'
                '        input_crc=crc32c::Extend(input_crc,key.data(),key.size());\n'
                '        input_crc=crc32c::Extend(input_crc,payload.data(),payload.size());\n'
                '        auto status=db_.db->Put(write_options_,key,payload);')
    body = once(body, '    }\n  }\n',
                '    }\n    fprintf(stderr,"Related generated input: keys=%llu crc32c=%u\\n",'
                '(unsigned long long)FLAGS_num,input_crc);\n  }\n')
    return (text[:start] + body + text[end:]).encode()


def lifetime_header():
    text = (COMMAND_ROOT / 'rrzc_sst_lifetime.h').read_text()
    text = '#include <cstdlib>\n' + text
    return once(text, '    return smoke?Plan{true,1024,1,256,16,32,32,persistent_h}:\n'
                '                 Plan{false,1048576,12,65536,4096,2048,8192,persistent_h};',
                '    uint64_t windows=12;\n'
                '    if (const char* value=std::getenv("RRZC_RELATED_WINDOWS")) {\n'
                '      char* end=nullptr; windows=std::strtoull(value,&end,10);\n'
                '      if (!*value || *end || windows<1 || windows>12 || smoke)\n'
                '        throw std::runtime_error("related prefix requires 1..12 full-size windows");\n'
                '    }\n'
                '    return smoke?Plan{true,1024,1,256,16,32,32,persistent_h}:\n'
                '                 Plan{false,1048576,windows,65536,4096,2048,8192,persistent_h};').encode()


def client_files():
    names = ('rrzc_end_to_end.h', 'rrzc_tls_seed.h', 'rrzc_front_order.h', 'rrzc_io_trace.h')
    files = {n: (COMMAND_ROOT / n).read_bytes() for n in names}
    files.update({'comparison_client.cc': client_source(), 'rrzc_sst_lifetime.h': lifetime_header()})
    return files


def builder(method):
    text = build_helper(method).decode()
    anchor = "    (target / 'build_complete.json').write_text("
    addition = r'''
    run([*command, '-I' + str(target), '-c', str(target / 'comparison_client.cc'),
         '-o', str(objects / 'comparison-client.o')], 'compile-comparison-client')
    run(['g++', 'tools/db_bench.o', str(objects / 'comparison-client.o'),
         'tools/tool_hooks.o', 'tools/simulated_hybrid_file_system.o', 'test_util/testutil.o',
         *built, str(library), '-Wl,--strip-debug', '-Wl,-Map,' + str(target / 'comparison-link.map'),
         '-lzbd', '-lpthread', '-lrt', '-ldl', '-lsnappy', '-lgflags', '-lz', '-lbz2',
         '-llz4', '-lzstd', '-u', 'zenfs_filesystem_reg', '-o', str(target / 'comparison_db_bench')],
        'link-comparison-client')
    linked = (target / 'comparison-link.map').read_text().split('Discarded input sections', 1)[0]
    assert all('librocksdb_debug.a(' + Path(unit).stem + '.o)' not in linked for unit in units)
    client_manifest = json.loads((target / 'comparison-client-identity.json').read_text())
    assert all(digest(target / n) == h for n, h in client_manifest.items())
    smoke_env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', ZENFS_RELATED_WORK=METHOD)
    if METHOD == 'oaza_gc': smoke_env['OAZA_GC_VARIANT'] = 'literal'
    if METHOD == 'coldmap': smoke_env['COLDMAP_FACTOR_DIRECTION'] = 'artifact-survival'
    run([str(target / 'comparison_db_bench'), '--db=' + str(target / 'comparison-posix'),
         '--benchmarks=rrzccalload,rrzcrelateddigest,verifychecksum', '--num=1024', '--value_size=4096',
         '--compression_type=none', '--disable_wal=1', '--cache_size=0', '--row_cache_size=0',
         '--seed=20261023', '--threads=1', '--compaction_pri=3'], 'comparison-posix', smoke_env)
    assert 'Related logical digest: keys=1024 ' in (target / 'comparison-posix.log').read_text()
    (target / 'comparison_build.json').write_text(json.dumps(dict(passed=True, method=METHOD,
        binary_sha256=digest(target / 'comparison_db_bench'), client_files=client_manifest,
        original_library_sha256=before, original_library_unchanged=digest(library) == before,
        affected_units=units, posix_passed=True, performance_experiment=False), indent=2) + '\n')
'''.replace('METHOD', repr(method))
    return once(text, anchor, addition + anchor).encode()


def make_payload(method, output, overlay=None):
    overlay = overlay or BASE / 'workspaces' / METHODS[method]
    files = client_files()
    files['comparison-client-identity.json'] = json.dumps({n: hashlib.sha256(b).hexdigest()
                                                        for n, b in files.items()}).encode()
    files['build_guest.py'] = builder(method)
    with tempfile.TemporaryDirectory(prefix='rrzc-comparison-payload-') as temp:
        old = Path(temp) / 'native.tar.gz'
        identity = payload_for(overlay, old, method)
        with tarfile.open(old) as source, tarfile.open(output, 'x:gz') as out:
            for member in source:
                if member.name in files: continue
                out.addfile(member, source.extractfile(member) if member.isfile() else None)
            for name, data in files.items():
                member = tarfile.TarInfo(name); member.size = len(data)
                out.addfile(member, io.BytesIO(data))
    return identity
