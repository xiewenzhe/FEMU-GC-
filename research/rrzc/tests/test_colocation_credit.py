"""校准入口的原资格、同负载边界和安全采证检查；无合成收益输出。"""
from pathlib import Path
import io
import subprocess
import tarfile
import tempfile
import unittest
from run_colocation_credit_host import wrapper,valid_tag,extract


class CreditTest(unittest.TestCase):
    def test_same_frozen_entry_no_oracle(self):
        text=wrapper();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        for key in ('ZENFS_RRZC_CANDIDATE_POOL=original','ZENFS_RRZC_UNIT_COST=1',
            'RRZC_CAL_GREEDY_EXECUTE="$greedy"','ZENFS_RRZC_ALL_BLOCKS=1'):
            self.assertIn(key,text)
        self.assertNotIn('ZENFS_RRZC_V1_SHADOW',text)
        self.assertIn('"$target/colocation_credit" "$scenario" "$result" "$k"',text)
        for tag in ('retire-k1-greedy-r1','live-k1-model-r2','smoke-k1-greedy-r1'):valid_tag(tag)
        for tag in ('live-k0-model-r1','../../逃逸','retire-k1-model-r3'):
            with self.assertRaises(ValueError):valid_tag(tag)

    def test_original_watermark_and_candidate_geometry(self):
        # 几何算术只验证载荷设计可行，不把预期复制量当真实实验。
        capacity=61*128;written=49*128;free=capacity-written
        self.assertEqual(free,1536)
        percent=100*free//capacity;threshold=100-3*(20-percent)
        self.assertEqual((percent,threshold),(19,97))
        mib=1<<20
        for valid in (3*mib//2,2*mib):
            garbage=100-100*valid//(128*mib)
            self.assertGreater(garbage,threshold)
            self.assertLess(garbage,100)
            self.assertGreater(100*((free+128)*mib-valid)//(capacity*mib),20)
        self.assertEqual(100-100*mib//(128*mib),100)
        self.assertLessEqual(100*(free+126-256)//capacity,20)

    def test_archive_rejects_cross_arm_links_and_nested_files(self):
        tag='retire-k1-greedy-r1'
        for path,kind in ((tag+'/深/日志',tarfile.REGTYPE),
                          (tag+'/aux',tarfile.SYMTYPE),('other/log',tarfile.REGTYPE)):
            with tempfile.TemporaryDirectory() as name:
                p=Path(name);archive=p/'evidence.tar.gz'
                with tarfile.open(archive,'w:gz') as t:
                    m=tarfile.TarInfo(path);m.type=kind;t.addfile(m,io.BytesIO())
                with self.assertRaises(ValueError):extract(archive,p,tag)
                self.assertFalse((p/tag).exists())


if __name__=='__main__':unittest.main()
