"""仅修改受控业务轨迹的生成检查，不把静态检查称为设备实验。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from all_blocks_followup import client_source,wrapper_source

ROOT=Path(__file__).resolve().parent
class FollowupTest(unittest.TestCase):
    def test_original_prefix_unchanged(self):
        old=(ROOT/'all_blocks_device.cc').read_text();new=client_source(old)
        start='static void run_case('
        boundary='  std::ofstream final(out+"/结束空间.csv");'
        prefix=old[old.index(start):old.index(boundary)]
        self.assertIn(prefix+'  followup(fs,d,logger,out,options,data,sources);\n',new)
        for forbidden in ('fs.Migrate','d.Reset','ForceGC','PerformReadReclaim'):
            self.assertNotIn(forbidden,new)
    def test_append_bytes_and_wait(self):
        text=client_source((ROOT/'all_blocks_device.cc').read_text())
        self.assertIn('i<4',text);self.assertIn('32*MiB,200+i',text)
        self.assertIn('seconds(12)',text);self.assertIn('verify_appended(fs,options,data,scratch)',text)
    def test_wrapper_syntax(self):
        original=(ROOT/'run_all_blocks_guest.sh').read_text();new=wrapper_source(original)
        self.assertIn('RRZC_FOLLOWUP_BRANCH="$scenario"',new)
        self.assertIn('all_blocks_device" opportunity',new)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'run.sh';p.write_text(new);subprocess.run(['bash','-n',str(p)],check=True)
        for line in original.splitlines():
            if 'ZENFS_' in line:self.assertIn(line,new)
    def test_wrong_source_rejected(self):
        with self.assertRaises(ValueError):client_source('')
        with self.assertRaises(ValueError):wrapper_source('')

if __name__=='__main__':unittest.main()
