"""真实数据库入口、显式标签和冻结对象边界；测试夹具不作为业务结果。"""
from pathlib import Path
import subprocess
import shutil
import tempfile
import tarfile
import io
import json
import hashlib
import unittest
from pool_dbbench_guest import roles,key,table,validate_source
from pool_dbbench_source import source,business
from run_pool_dbbench_host import DONOR,valid_tag,extract,verified_database,freeze_verified_programs
from pool_dbbench_sync_source import patched as sync_patch,require_ready
from analyze_pool_dbbench import space_handoff


class PoolDbbenchTest(unittest.TestCase):
    def test_actual_wrappers_have_same_model_and_safe_replay(self):
        root=DONOR/DONOR.name.removesuffix('-results')
        if not root.exists():self.skipTest('归档源缺失')
        a=source((root/'run_logical_source_guest.sh').read_text())
        b=business((root/'run_calibrated_guest.sh').read_text())
        for text in (a,b):
            subprocess.run(['bash','-n'],input=text,text=True,check=True)
            self.assertIn('ZENFS_RRZC_POOL_K=1',text)
            self.assertIn('ZENFS_RRZC_MAINTENANCE_COVER=1',text)
            self.assertIn('ZENFS_RRZC_CANDIDATE_POOL=original',text)
            self.assertIn('--disable_wal=1 --cache_size=0 --row_cache_size=0',text)
            self.assertNotIn('RRZC_CAL_IO_TRACE=',text)
        self.assertLess(a.index('pool_dbbench_guest.py'),a.index('logical_input_guest.py" capture'))
        self.assertIn('"$target/p1" matched',b)
        self.assertIn('--reads=786432 --writes=589824',b)
        self.assertIn('RRZC_CAL_GREEDY_EXECUTE="$cal_greedy_execute"',b)

    def test_real_preparation_sst_keys_produce_both_roles_without_future(self):
        p=DONOR/DONOR.name.removesuffix('-results')/'source/装载-0.sst.csv'
        if not p.is_file():self.skipTest('真实准备期SST清单缺失')
        labels=roles(table(p));self.assertEqual(set(labels.values()),{'H','W'})
        self.assertTrue(all(n.startswith('/rrzc-dbbench/') and n.endswith('.sst') for n in labels))
        self.assertGreater(len(labels),2)

    def test_key_encoding_and_out_of_range_rejected(self):
        self.assertEqual(key('00000000000010003030303030303030'),4096)
        with self.assertRaises(AssertionError):key('00')
        with self.assertRaises(AssertionError):key('00000000000010003131313131313131')

    def test_cohort_zero_includes_all_W0_and_excludes_other_windows(self):
        p=DONOR/DONOR.name.removesuffix('-results')/'source/装载-0.sst.csv'
        if not p.is_file():self.skipTest('真实准备期SST清单缺失')
        rows=table(p);full=roles(rows);limited=roles(rows,True)
        self.assertEqual(list(limited.values()).count('H'),2)
        self.assertEqual(list(limited.values()).count('W'),30)
        self.assertTrue(all(full[n]==tag for n,tag in limited.items()))
        for r in rows:
            path='/rrzc-dbbench'+r['文件']
            if key(r['最小键十六进制'])>=65536:self.assertNotIn(path,limited)
        # 七个尚未退役的W0也必须进入清单，不能只挑已知失效的23个。
        for number in range(33,40):self.assertEqual(limited[f'/rrzc-dbbench/{number:06}.sst'],'W')

    def test_cohort_validation_uses_declared_scope_and_exact_runtime_tags(self):
        from run_pool_dbbench_host import VERIFIED_DB
        old=VERIFIED_DB/VERIFIED_DB.name.removesuffix('-results')/'source'
        if not old.exists():self.skipTest('已验收协同布局缺失')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'source').mkdir()
            for name in ('装载-0.sst.csv','装载-0.extent.csv'):
                shutil.copyfile(old/name,root/'source'/name)
            (root/'source/load.zenfs.log').write_text('协同池取得目标：阶段=新文件\n')
            rows=table(root/'source/装载-0.sst.csv');labels=roles(rows,True)
            (root/'受控标签来源.json').write_text(json.dumps(dict(标签范围='窗口0',标签=labels,参考SST=rows)))
            (root/'受控标签.txt').write_text(''.join(n+' '+v+'\n' for n,v in sorted(labels.items())))
            validate_source(root)
            self.assertEqual(json.loads((root/'真实SST布局核验.json').read_text())['标签数'],32)
            (root/'受控标签.txt').write_text('错误清单\n')
            with self.assertRaises(AssertionError):validate_source(root)

    def test_phase_whitelist_and_no_algorithm_compile(self):
        for tag in ('source','p1','p2','m1','m2'):valid_tag(tag)
        for tag in ('../bad','p3','g1','m3'):
            with self.assertRaises(ValueError):valid_tag(tag)
        build=(Path(__file__).parent/'build_pool_dbbench_guest.py').read_text()
        self.assertNotIn("'-W','plugin/zenfs/fs/fs_zenfs.cc'",build)
        self.assertIn("str(obj),*supports",build)
        self.assertIn("for name,sha in expected.items()",build)

    def test_build_helpers_import_in_isolated_deployment(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for name in ('build_pool_dbbench_guest.py','build_all_blocks_guest.py',
                         'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py',
                         'build_pool_dbbench_sync_guest.py','pool_dbbench_sync_source.py',
                         'controlled_gc_source.py','target_alloc_source.py'):
                shutil.copyfile(Path(__file__).parent/name,folder/name)
            subprocess.run(['python3','-I','-c',
                'import sys;sys.path.insert(0,sys.argv[1]);import build_pool_dbbench_guest;import build_pool_dbbench_sync_guest',str(folder)],check=True)

    def test_space_handoff_rejects_polling_and_early_ack(self):
        good='受控空间请求：序号=1，重试=1\n受控GC紧急检查：上一完成批次=12，空间请求=1\n受控空间维护完成：序号=1，完成=1\n受控空间确认：序号=1，重试=1\n'
        self.assertEqual(space_handoff(good)['请求数'],1)
        early=good.split('受控空间维护完成：',1)[0]+'受控空间确认：序号=1，重试=1\n受控空间维护完成：序号=1，完成=1\n'
        for bad in (good.replace('空间请求=1','等待数=1'),good.replace('受控空间维护完成：','丢失：'),
                    early,
                    good.replace('受控空间维护完成：','临时：').replace('受控空间确认：','受控空间维护完成：')+'受控空间确认：序号=1，ok\n受控空间确认：序号=1，重复\n'):
            with self.assertRaises(AssertionError):space_handoff(bad)

    def test_real_db_nested_regular_files_allowed_but_links_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'good.tar'
            with tarfile.open(archive,'w') as t:
                m=tarfile.TarInfo('p1/回放前辅助文件/rrzc-dbbench/LOG');m.size=3;t.addfile(m,io.BytesIO(b'abc'))
            extract(archive,root,'p1')
            self.assertEqual((root/'p1/回放前辅助文件/rrzc-dbbench/LOG').read_bytes(),b'abc')
            for i,path in enumerate(('p2/../bad','/p2/bad','other/file','p2/link')):
                bad=root/f'bad{i}.tar'
                with tarfile.open(bad,'w') as t:
                    m=tarfile.TarInfo(path)
                    if path.endswith('link'):m.type=tarfile.SYMTYPE
                    t.addfile(m,io.BytesIO())
                with self.assertRaises(ValueError):extract(bad,root,'p2')
                self.assertFalse((root/'p2').exists())

    def test_real_frozen_pool_can_reuse_verified_space_protocol(self):
        base=DONOR.parent/'joint-controlled-maintenance-cover-v1-20261029-results/joint-controlled-maintenance-cover-v1-20261029/协同池副本/plugin/zenfs/fs'
        if not base.is_dir():self.skipTest('冻结协同源码缺失')
        fs=(base/'fs_zenfs.cc').read_text();zbd=(base/'zbd_zenfs.cc').read_text()
        with self.assertRaises(ValueError):require_ready(fs,zbd)
        a,b=sync_patch(fs,zbd)
        require_ready(a,b)
        self.assertIn('rrzc_boundary::State().Pending()',a)
        self.assertIn('rrzc_boundary::State().Complete(sequence)',a)
        self.assertIn('rrzc_boundary::State().Request()',b)
        self.assertIn('rrzc_boundary::State().Wait(request,boundary_deadline)',b)
        self.assertNotIn('now>=next_emergency',a)
        self.assertEqual(fs.split('    uint64_t non_free = zbd_->GetUsedSpace()',1)[1],
                         a.split('    uint64_t non_free = zbd_->GetUsedSpace()',1)[1])
        self.assertEqual(zbd.count('TryColocation('),b.count('TryColocation('))
        with self.assertRaises(ValueError):sync_patch(a,b)

    def test_verified_reuse_checks_archive_program_and_repeat_gate(self):
        # 合成夹具仅测试准入拒绝分支，不作为真实实验通过证据。
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);guest=root/'trial';fs=guest/'协同池副本/plugin/zenfs/fs';fs.mkdir(parents=True)
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            def save(name,value):(root/name).write_text(json.dumps(value))
            pack=root/'archive';pack.write_bytes(b'archive')
            (guest/'db_bench').write_bytes(b'program');(guest/'space_probe').write_bytes(b'probe')
            (guest/'fs_colocation.o').write_bytes(b'object')
            (fs/'fs_zenfs.cc').write_text('rrzc_boundary::State().Pending() rrzc_boundary::State().Complete(sequence)')
            (fs/'zbd_zenfs.cc').write_text('rrzc_boundary::State().Request() rrzc_boundary::State().Wait(request,boundary_deadline)')
            save('状态.json',dict(标签='trial',当前客机PID=None,镜像处理=dict(archive=str(pack),archive_sha256=sha(pack))))
            save('阶段归档.json',dict(完整=True,业务成功=True,客机仍存活=False))
            save('同步补接验收.json',dict(通过=True,归档SHA256=sha(pack)));save('Greedy重复核验.json',dict(通过=True))
            build=dict(通过=True,冻结ZenFS对象SHA256=sha(guest/'fs_colocation.o'),原库SHA256='library',db_benchSHA256=sha(guest/'db_bench'))
            (guest/'真实DB构建核验.json').write_text(json.dumps(build))
            (guest/'同步接线构建核验.json').write_text(json.dumps(dict(通过=True,真实线程协议自检通过=True,新对象SHA256=build['冻结ZenFS对象SHA256'])))
            self.assertEqual(verified_database(root)[0],guest)
            out=root/'new';(out/'源码').mkdir(parents=True)
            freeze_verified_programs(out,root,guest,build)
            self.assertEqual((out/'源码/db_bench.frozen').read_bytes(),(guest/'db_bench').read_bytes())
            self.assertFalse((out/'源码/同步重编清单.json').exists())
            save('Greedy重复核验.json',dict(通过=False))
            with self.assertRaises(ValueError):verified_database(root)
            save('Greedy重复核验.json',dict(通过=True));(guest/'db_bench').write_bytes(b'changed')
            with self.assertRaises(ValueError):verified_database(root)


if __name__=='__main__':unittest.main()
