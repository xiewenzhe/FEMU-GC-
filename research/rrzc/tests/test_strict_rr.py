"""严格RR纯核、设备协议、主机接线及双方同规则的静态与回归测试。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import json
from unittest.mock import patch
import run_strict_ycsb_host as host

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT.parents[1]

class StrictRRTest(unittest.TestCase):
    def test_pure_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'unit'
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(PROJECT/'hw/femu/zns'),str(ROOT/'tests/test_rr_strict.c'),'-o',str(binary)],check=True)
            self.assertIn('其他块计数',subprocess.check_output([str(binary)],text=True))

    def test_guest_scripts_and_default_isolation(self):
        for name in ('build_strict_rr_guest.sh','build_strict_ycsb_guest.sh','run_strict_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(len({p.name for p in host.FILES}),len(host.FILES))
        wrapper=(ROOT/'tests/run_strict_ycsb_guest.sh').read_text()
        self.assertIn('ZENFS_RR_STRICT=1',wrapper)
        self.assertIn('ZENFS_RR_STRICT_AUDIT=1',wrapper)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=0',wrapper)
        self.assertLess(wrapper.index('strict.business.json'),wrapper.index('"$target/verify_ycsb"'))
        self.assertNotIn('ZENFS_RR_STRICT=1',(ROOT/'tests/run_expected_ycsb_guest.sh').read_text())
        props=(ROOT/'tests/workload-stable-unlimited.properties').read_text()
        self.assertIn('limit.ops=0',props);self.assertNotIn('limit.file=',props)
        verifier=(ROOT/'tests/strict/verify_ycsb.cc').read_text()
        self.assertIn('device->Open(false,true)',verifier)
        self.assertIn('fs->Mount(false)',verifier)
        self.assertIn('DB::OpenForReadOnly',verifier)
        self.assertIn('logger->SetInfoLogLevel(DEBUG_LEVEL)',verifier)

    def test_lock_exit_before_synchronous_refresh(self):
        patch=(ROOT/'zenfs-strict-rr.patch').read_text()
        self.assertIn('PositionedReadOnce(offset,n,result,scratch,direct)',patch)
        self.assertIn('const auto refresh=zbd_->ServiceStrictRR()',patch)
        self.assertIn('rrzc_strict::MigrationScope migration_scope',patch)
        self.assertIn('status & 0x7ff',patch)
        self.assertIn('StrictLock()',patch)

    def test_counter_protocol_layout(self):
        from strict_stats_guest import Command
        import ctypes
        self.assertEqual(ctypes.sizeof(Command),72)

    def test_analysis_rejects_overflow_missing_coverage_and_false_accounts(self):
        # 合成归档仅验证拒绝条件，不能称作真实设备结果。
        import analyze_expected_ycsb as analysis
        values=dict(版本=1,启用=1,实际NAND读取=21,计入RC读取=20,自身刷新豁免读取=1,
            其他块搬迁计数读取=2,阈值触发事件=1,被阻止读取=0,历史最高RC=1024)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'zenfs.run.log').write_text('')
            (root/'zones.final.json').write_text(json.dumps(dict(zone_list=[dict(state='EMPTY')])))
            def save(v):
                for name in ('strict.business.json','strict.after-verify.json'):
                    (root/name).write_text(json.dumps(v))
            save(values)
            with patch.object(analysis,'shared_arm',return_value=dict(RR次数=1)):
                self.assertEqual(analysis.analyze_arm(root,'short','expected',strict_rr=True)['严格RR业务账目'],values)
            for key,value in (('历史最高RC',1025),('阈值触发事件',0),('实际NAND读取',22),
                              ('自身刷新豁免读取',0),('其他块搬迁计数读取',0)):
                changed=dict(values);changed[key]=value;save(changed)
                with self.subTest(key=key),patch.object(analysis,'shared_arm',return_value=dict(RR次数=1)),self.assertRaises(ValueError):
                    analysis.analyze_arm(root,'short','expected',strict_rr=True)

if __name__=='__main__':unittest.main()
