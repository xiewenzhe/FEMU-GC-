"""冻结副本补丁及晚Full历史回归；测试输入不作为真实设备结果。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from all_blocks_scope_source import patched,make_patch,DIAG

ROOT=Path(__file__).resolve().parents[1]
FROZEN=Path('/home/data/xwz/rrzc-baselines/joint-controlled-pool-dbbench-v4-20261029-results/joint-controlled-pool-dbbench-v4-20261029/协同池副本/plugin/zenfs/fs')


class ScopeTest(unittest.TestCase):
    def test_frozen_patch_is_reversible_and_does_not_modify_victim_body(self):
        fs=(FROZEN/'fs_zenfs.cc').read_text();inc=(FROZEN/'rrzc_direct_zenfs.inc').read_text()
        a,b=patched(fs,inc)
        self.assertEqual(b.replace(DIAG+'\n',''),inc)
        self.assertEqual(a.split('    uint64_t non_free =',1)[1],fs.split('    uint64_t non_free =',1)[1])
        self.assertIn('(!sample_partial && !zone->IsFull())',a)
        with self.assertRaises(ValueError):patched(a,b)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'fs').mkdir()
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(FROZEN/name,folder/'fs'/name)
            patch=folder/'change.patch';patch.write_text(make_patch(fs,inc))
            for flags in (['--check'],[],['--reverse','--check'],['--reverse']):
                subprocess.run(['git','apply',*flags,str(patch)],cwd=folder,check=True)
            self.assertEqual((folder/'fs/fs_zenfs.cc').read_text(),fs)
            self.assertEqual((folder/'fs/rrzc_direct_zenfs.inc').read_text(),inc)

    def test_late_full_and_idle_are_not_the_same_risk_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'test'
            subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_all_blocks_late_full.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_representative_log_compiles_against_actual_types(self):
        code=r'''#include "rrzc_all_blocks.h"
#include <cstdarg>
void Info(void*,const char*,...) __attribute__((format(printf,2,3)));
void Info(void*,const char*,...) {}
int main(){
bool all_blocks_diagnostic=true;void* logger_=nullptr;uint64_t shadow_round=12;
rrzc_simple::Candidate c;rrzc_all_blocks::Evidence proof;
std::map<uint64_t,rrzc_v1::RecentBlock> usable;auto b=usable.find(0);
std::map<uint64_t,rrzc_all_blocks::Report> all_blocks_current;auto found=all_blocks_current.find(0);
rrzc_all_blocks::Store::Histories all_blocks_frozen;auto history=all_blocks_frozen.find(0);
'''+DIAG+'\n}\n'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'diag.cc';source.write_text(code);binary=Path(tmp)/'diag'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Wformat=2','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)


if __name__=='__main__':unittest.main()
