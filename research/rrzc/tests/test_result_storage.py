"""Synthetic fixtures verify retention boundaries; no performance claims."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import result_storage as m
import run_calibration_host as calibration


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))


def fixture(base, label='joint-controlled-source', data=b'logical-input'):
    output = base/(label+'-results');root = output/label
    (root/'逻辑输入').mkdir(parents=True)
    (root/'p1').mkdir()
    (root/'逻辑输入/1.bin.gz').write_bytes(data)
    write_json(root/'逻辑输入/清单.json', dict(逐区=[dict(Zone=1, 压缩SHA256=m.digest(root/'逻辑输入/1.bin.gz'))]))
    (root/'p1/run.log').write_text('raw maintenance evidence')
    archive = output/(label+'-calibration.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:tar.add(root, arcname=label)
    write_json(output/'状态.json', dict(标签=label, 当前客机PID=None,
        镜像处理=dict(status='deleted', archive=str(archive), archive_sha256=m.digest(archive))))
    write_json(output/'阶段归档.json', dict(完整=True, 客机仍存活=False, 业务成功=True))
    return output, root, archive


class StorageTest(unittest.TestCase):
    def test_maintenance_blocks_new_experiment_admission(self):
        with tempfile.TemporaryDirectory() as folder:
            m.check_maintenance(folder)
            with m.maintenance_guard(folder):
                with self.assertRaisesRegex(Exception,'正在整理'):
                    m.check_maintenance(folder)
            m.check_maintenance(folder)

    def test_source_observation_mismatch_blocks_cross_run_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            output, _, _ = fixture(Path(folder))
            state=json.loads((output/'状态.json').read_text())
            state['设备命令日志']='original-ftl-log'
            write_json(output/'状态.json',state)
            with self.assertRaisesRegex(Exception, '追踪开关不同'):
                m.check_observation(output,False)
            m.check_observation(output,True)

    def test_checkpoint_limit_counts_pending_and_archived(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            m.reserve_checkpoint(base, 'joint-controlled-a', base/'a')
            m.reserve_checkpoint(base, 'joint-controlled-b', base/'b')
            m.reserve_checkpoint(base, 'joint-controlled-a', base/'a')
            with self.assertRaisesRegex(Exception, '已达2份'):
                m.reserve_checkpoint(base, 'joint-controlled-c', base/'c')

    def test_shared_input_archive_keeps_evidence_and_follows_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder);source, _, source_archive = fixture(base)
            output, root, original = fixture(base, 'joint-controlled-repeat')
            old_sha = m.digest(original)
            result = m.share_archived_input(output, source)
            state, key = m.closed_archive(output)
            self.assertFalse(original.exists())
            self.assertTrue(source_archive.exists())
            self.assertEqual(state['镜像处理']['original_archive_sha256'], old_sha)
            self.assertEqual(m.replay_source(output)['output'], str(source))
            with tarfile.open(key) as tar:
                self.assertNotIn(root.name+'/逻辑输入/1.bin.gz', tar.getnames())
                self.assertEqual(tar.extractfile(root.name+'/p1/run.log').read(), b'raw maintenance evidence')
                self.assertIn(root.name+'/逻辑输入/清单.json', tar.getnames())

    def test_different_input_refuses_compaction_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder);source, _, _ = fixture(base)
            output, _, original = fixture(base, 'joint-controlled-repeat', b'different')
            before = (output/'状态.json').read_bytes()
            with self.assertRaisesRegex(Exception, '输入字节与共享来源不同'):
                m.share_archived_input(output, source)
            self.assertTrue(original.exists())
            self.assertEqual((output/'状态.json').read_bytes(), before)
            self.assertFalse(list(output.glob('*.partial')))

    def test_input_manifest_alone_is_not_a_replay_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            output, root, archive = fixture(Path(folder))
            with tarfile.open(archive,'w:gz') as tar:
                tar.add(root/'逻辑输入/清单.json',arcname=root.name+'/逻辑输入/清单.json')
            state=json.loads((output/'状态.json').read_text())
            state['镜像处理']['archive_sha256']=m.digest(archive)
            write_json(output/'状态.json',state)
            with self.assertRaisesRegex(Exception,'缺少固定输入字节'):
                m.replay_source(output)

    def test_dedup_keeps_paths_and_different_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            output, root, _ = fixture(Path(folder))
            dest = output/'逐臂采证/p1';dest.mkdir(parents=True)
            shutil.copyfile(root/'p1/run.log', dest/'run.log')
            (root/'p1/run.log').chmod(0o644);(dest/'run.log').chmod(0o664)
            (root/'p1/other.log').write_text('left')
            (dest/'other.log').write_text('right')
            result = m.deduplicate_collected(output, root.name)
            self.assertEqual(result['files'], 1)
            self.assertEqual((root/'p1/run.log').stat().st_ino, (dest/'run.log').stat().st_ino)
            self.assertEqual((dest/'run.log').stat().st_mode&0o777,0o644)
            self.assertEqual((dest/'other.log').read_text(), 'right')
            self.assertEqual(m.deduplicate_collected(output, root.name)['files'], 0)

    def test_verified_transfer_removed_and_source_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder);source, root, archive = fixture(base)
            output = base/'repeat';(output/'源码').mkdir(parents=True)
            payload = output/'源码/固定输入复用.tar'
            paths = {'逻辑输入/'+p.name: p for p in (root/'逻辑输入').iterdir()}
            with tarfile.open(payload, 'w') as tar:
                for name, path in paths.items():tar.add(path, arcname=name)
            write_json(output/'源码/固定输入复用清单.json', dict(来源=str(source),
                来源归档SHA256=m.digest(archive), 压缩包SHA256=m.digest(payload),
                文件={name:m.digest(path) for name,path in paths.items()}))
            self.assertGreater(m.remove_input_transfer(output), 0)
            self.assertFalse(payload.exists());self.assertTrue(archive.exists())

    def test_compact_losslessly_compresses_ftl_after_close(self):
        with tempfile.TemporaryDirectory() as folder:
            output, _, archive = fixture(Path(folder))
            original = b'RRZCFT01'+bytes(6400)
            (output/'FEMU设备命令.bin').write_bytes(original)
            m.compact(output)
            self.assertFalse((output/'FEMU设备命令.bin').exists())
            with gzip.open(output/'FEMU设备命令.bin.gz', 'rb') as stream:
                self.assertEqual(stream.read(), original)
            self.assertTrue(archive.exists())

    def test_archive_exclusion_retains_input_reference_and_key_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder);source, source_root, archive = fixture(base)
            out = base/'output';(out/'源码').mkdir(parents=True)
            sha = m.digest(source_root/'逻辑输入/1.bin.gz')
            write_json(out/'源码/固定输入复用清单.json', dict(文件={'逻辑输入/1.bin.gz':sha},
                       来源归档SHA256=m.digest(archive)))
            guest_base = base/'guest';guest = guest_base/'joint-controlled-repeat'
            (guest/'逻辑输入').mkdir(parents=True);(guest/'p1').mkdir()
            (guest/'逻辑输入/1.bin.gz').write_bytes(b'logical-input')
            (guest/'逻辑输入/清单.json').write_text('{}')
            (guest/'p1/run.log').write_text('key evidence')
            (guest/'固定输入复用.tar').write_bytes(b'transport-only')
            run = object.__new__(calibration.CalibrationRun)
            run.output=out;run.target='/home/femu/'+guest.name;run.tag='calibration'
            run.state={'当前客机PID':None};run.deployed=True;run.socket=None
            run.record=lambda **values:run.state.update(values)
            m.configure(run,input_source=source)
            remote = guest_base/(guest.name+'-calibration.tar.gz')
            def ssh(command,*args):
                if command.startswith('sha256sum'):return m.digest(remote)+'  archive'
                subprocess.run(command.replace('/home/femu',str(guest_base)),shell=True,check=True)
                return ''
            def command(argv,*args):
                if argv[0]=='scp':shutil.copyfile(remote,out/remote.name)
                else:subprocess.run(argv,check=True)
            run.ssh=ssh;run.command=command;run.scp=lambda:['scp'];run.close_guest=lambda:None
            def finished(path,expected,**kwargs):
                run.state['镜像处理']=dict(archive=str(path),archive_sha256=expected)
            run.finish_image=finished
            run.finish()
            with tarfile.open(out/remote.name) as tar:
                self.assertNotIn(guest.name+'/逻辑输入/1.bin.gz',tar.getnames())
                self.assertNotIn(guest.name+'/固定输入复用.tar',tar.getnames())
                self.assertIn(guest.name+'/逻辑输入/清单.json',tar.getnames())
                self.assertIn(guest.name+'/p1/run.log',tar.getnames())
            self.assertEqual(run.state['镜像处理']['archive_input_reference']['output'],str(source))

    def test_disabled_ftl_keeps_binary_and_clears_inherited_trace_env(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'qemu-system-x86_64';binary.write_bytes(b'same-binary')
            run=object.__new__(calibration.CalibrationRun)
            run.output=root;run.ftl_audit_binary=str(binary);run.ftl_audit_sha256=m.digest(binary)
            run.ftl_audit_enabled=False;run.record=lambda **values:None
            observed={}
            def command(self,argv,timeout=60):
                observed.update(binary=argv[0],env=os.environ.get('FEMU_FTL_AUDIT'))
            with patch.dict(os.environ,{'FEMU_FTL_AUDIT':'unexpected-parent-log'}), \
                 patch.object(calibration.ReuseRun,'command',command):
                run.command(['qemu-system-x86_64','-m','8G'])
                self.assertEqual(os.environ['FEMU_FTL_AUDIT'],'unexpected-parent-log')
            self.assertEqual(observed,dict(binary=str(binary),env=None))


if __name__=='__main__':unittest.main()
