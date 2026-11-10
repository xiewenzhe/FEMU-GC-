import unittest
from export_source_guest import source_names


class SourceSelectionTest(unittest.TestCase):
    def test_excludes_untracked_binaries_logs_and_databases_but_keeps_source(self):
        cached = b'Makefile\0db/version_set.cc\0include/rocksdb/file_system.h\0'
        untracked = (b'plugin/zenfs/util/zenfs.dbg\0db_bench\0librocksdb_debug.a\0'
                     b'build.log\0test-db/000123.sst\0plugin/zenfs/fs/custom.h\0'
                     b'plugin/zenfs/fs/custom.inc\0build_detect.sh\0')
        self.assertEqual(source_names(cached, untracked), sorted([
            'Makefile', 'db/version_set.cc', 'include/rocksdb/file_system.h',
            'plugin/zenfs/fs/custom.h', 'plugin/zenfs/fs/custom.inc', 'build_detect.sh']))


if __name__ == '__main__':
    unittest.main()
