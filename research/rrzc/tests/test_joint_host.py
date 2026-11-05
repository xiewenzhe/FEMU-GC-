"""受控短测入口回归：配置隔离、零门槛补丁及真实命令替换；不伪称设备收益。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import run_joint_host as host


class JointHostTest(unittest.TestCase):
    def test_scaled_profile_preserves_zone_and_target(self):
        target=host.load();scaled=host.controlled_profile()
        self.assertEqual(target['命名空间']['容量字节'],32 << 30)
        self.assertEqual(scaled['命名空间']['容量字节'],4 << 30)
        self.assertEqual(scaled['命名空间']['Zone字节'],128 << 20)
        self.assertEqual(scaled['命名空间']['每Zone物理块数'],128)
        self.assertEqual(scaled['维护'],target['维护'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile.json';path.write_text(json.dumps(scaled))
            self.assertEqual(host.load(path),scaled)

    def test_command_replaces_only_device(self):
        run=host.JointRun.__new__(host.JointRun);run.profile=host.controlled_profile()
        argv=['./build-femu/qemu-system-x86_64','-device',host.OLD_DEVICE,'-m','4G']
        with patch.object(host.FixedPair,'command',return_value='已拦截，不启动') as call:
            self.assertEqual(run.command(argv),'已拦截，不启动')
        sent=call.call_args.args[0]
        self.assertIn('devsz_mb=4096',sent[2]);self.assertIn('zns_rr_threshold=1024',sent[2])
        self.assertEqual(argv[2],host.OLD_DEVICE)
        self.assertEqual(sent[:2]+sent[3:],argv[:2]+argv[3:])
        with self.assertRaises(RuntimeError):run.command([argv[0],'-device','不是已知旧参数'])

    def test_zero_patch_on_real_parent_and_reverse(self):
        parent=host.IMAGE_DIR/'content-guard-20261020-results/build/content-guard-20261020/fs_zenfs.compiled.cc'
        if not parent.is_file():self.skipTest('没有真实父源码，不模拟补丁验证')
        patch_text=(host.PROJECT/'research/rrzc/zenfs-zero-reclaim.patch').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'fs').mkdir();dest=root/'fs/fs_zenfs.cc';dest.write_bytes(parent.read_bytes())
            subprocess.run(['git','apply','--unidiff-zero'],input=patch_text,text=True,cwd=tmp,check=True,capture_output=True)
            self.assertNotIn("*end == '\\0' && simple_budget > 0",dest.read_text())
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch_text,text=True,cwd=tmp,check=True,capture_output=True)
            self.assertEqual(dest.read_bytes(),parent.read_bytes())

    def test_scripts_parse_and_no_synthetic_rc_or_direct_maintenance(self):
        tests=host.PROJECT/'research/rrzc/tests'
        subprocess.run(['bash','-n',str(tests/'build_joint_guest.sh'),str(tests/'run_joint_guest.sh')],check=True)
        source=(tests/'joint_opportunity_smoke.cc').read_text()
        for forbidden in ('->Reset(','.Reset(','.GCWorker(','.RRWorker(','.Finish(','->Finish('):
            self.assertNotIn(forbidden,source)
        wrapper=(tests/'run_joint_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=0',wrapper)
        self.assertIn('ZENFS_RR_ENABLE=1',wrapper)
        self.assertIn('ZENFS_RR_ENABLE=0',wrapper)
        self.assertNotIn('zcap',wrapper)

    def test_verification_uses_tail_offset_and_full_rr_scan_window(self):
        source=(host.PROJECT/'research/rrzc/tests/joint_opportunity_smoke.cc').read_text()
        self.assertIn('expected.get()+off%MiB',source)
        self.assertIn('constexpr unsigned JointFillers=22;',source)
        self.assertIn('seconds(25)',source)
        self.assertIn('==17',source)
        self.assertIn('==24',source)
        self.assertIn('seconds(33)',source)


if __name__=='__main__':unittest.main()
