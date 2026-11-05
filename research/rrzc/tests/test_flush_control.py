"""来源对照拒绝错程序、损坏包和关闭时仍有标签；合成夹具不作实验结果。"""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from flush_control_bundle import disabled_guard
from reuse_flush_origin_guest import restore, unpack


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


class ControlTest(unittest.TestCase):
    def test_disabled_requires_flush_but_rejects_any_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for n in ('run.log','verify.log','run.zenfs.log','verify.zenfs.log'):(p/n).write_text('')
            log=p/'run.rocksdb.log';log.write_text('EVENT_LOG_v1 '+json.dumps(dict(event='flush_finished',job=1)))
            disabled_guard(p)
            (p/'verify.zenfs.log').write_text('Flush来源挂载文件：不应存在')
            with self.assertRaises(ValueError):disabled_guard(p)
            (p/'verify.zenfs.log').write_text('');log.write_text('')
            with self.assertRaises(ValueError):disabled_guard(p)

    def fixture(self,p):
        target=p/'target';target.mkdir();native=p/'native';native.mkdir()
        lib=native/'librocksdb_debug.a';lib.write_bytes(b'fixture-library')
        (target/'expected.json').write_text(json.dumps({'librocksdb_debug.a':sha(lib)}))
        files=p/'files';files.mkdir()
        for n in ('db_bench','space_probe','flush_metadata_probe','校准-db_bench_tool.cc',
                  '诊断-flush_job.cc','rrzc_sst_lifetime.h','fs_colocation.o'):
            (files/n).write_text(n);(files/n).chmod(0o755)
        build=dict(通过=True,元数据版本=11,ZenFS完整五单元重编=True,程序SHA256=sha(files/'db_bench'),
                   新客户端源码SHA256=sha(files/'校准-db_bench_tool.cc'),冻结ZenFS对象SHA256=sha(files/'fs_colocation.o'))
        (files/'生命周期构建核验.json').write_text(json.dumps(build))
        fs=files/'协同池副本/plugin/zenfs/fs';fs.mkdir(parents=True);(fs/'io_zenfs.h').write_text('新ABI夹具')
        def pack(name,names):
            path=target/(name+'.tar.gz')
            with tarfile.open(path,'w:gz') as tar:
                for n in names:tar.add(files/n,arcname=n,recursive=False)
            return dict(压缩包SHA256=sha(path),文件={n:sha(files/n) for n in names})
        bundle=pack('冻结对象',['fs_colocation.o','协同池副本/plugin/zenfs/fs/io_zenfs.h'])
        bundle.update(对象SHA256=sha(files/'fs_colocation.o'),原库SHA256=sha(lib))
        (target/'冻结对象清单.json').write_text(json.dumps(bundle))
        plan=pack('Flush来源复用',['db_bench','space_probe','flush_metadata_probe','校准-db_bench_tool.cc',
                  '诊断-flush_job.cc','rrzc_sst_lifetime.h','生命周期构建核验.json'])
        plan.update(来源='仅合成测试夹具',对象SHA256=bundle['对象SHA256'],原库SHA256=sha(lib),
                    程序SHA256=build['程序SHA256'],请求源码SHA256=build['新客户端源码SHA256'])
        (target/'Flush来源复用清单.json').write_text(json.dumps(plan))
        (target/'db_bench').write_text('原恢复程序')
        return target,native,plan

    def test_restore_same_binary_keeps_old_copy_and_does_not_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            target,native,plan=self.fixture(Path(tmp));restore(target,native)
            self.assertEqual(sha(target/'db_bench'),plan['程序SHA256'])
            self.assertEqual((target/'来源复用前/db_bench').read_text(),'原恢复程序')
            build=json.loads((target/'生命周期构建核验.json').read_text())
            self.assertFalse(build['本轮重新编译']);self.assertFalse(build['本轮重新运行普通目录小测'])

    def test_wrong_abi_or_corrupted_package_stops_before_client_replacement(self):
        for kind in ('abi','package'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                target,native,plan=self.fixture(Path(tmp))
                if kind=='abi':
                    plan['对象SHA256']='错误ABI'
                    (target/'Flush来源复用清单.json').write_text(json.dumps(plan))
                else:(target/'Flush来源复用.tar.gz').write_bytes('损坏包'.encode())
                with self.assertRaises(AssertionError):restore(target,native)
                self.assertEqual((target/'db_bench').read_text(),'原恢复程序')

    def test_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'item').write_text('夹具')
            with tarfile.open(p/'test.tar.gz','w:gz') as tar:tar.add(p/'item',arcname='../escape')
            manifest=dict(压缩包SHA256=sha(p/'test.tar.gz'),文件={'../escape':sha(p/'item')})
            with self.assertRaises(AssertionError):unpack(p/'test.tar.gz',manifest,p/'out',lambda _:True)
            self.assertFalse((p/'out').exists())


if __name__=='__main__':unittest.main()
