"""独立十进制核验、身份边界和三个真实反例重放；不伪造闭环效果。"""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from replay_expected_rr import calculate, probability, replay_log, SECOND, SCALE

ARCHIVE=Path('/home/data/xwz/rrzc-baselines/joint-persistent-cases-v1-20261022-results')


class ExpectedRRTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-expected-test-');cls.binary=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_expected_rr_selector.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def row(self):
        return [128,10*2**20,128*2**20,976,976,976,7,100*SECOND,1024,3,
            1,128,3,10*2**20,0,10*SECOND,0,30*SECOND,1,16,SECOND]

    def test_cpp_safety_and_direct_rank(self):
        self.assertIn('不声称真实RR收益',subprocess.check_output([str(self.binary)],text=True))

    def test_probability_independent_decimal_cdf(self):
        pairs=[(0,0),(1,0),(1,Fraction(1,10)),(1,1),(1,1000),(48,160),
            (1024,1024),(1024,1000),(1024,1200),(100001,1),(2,10**30),
            (1,-1),(99999,10**6)]
        rng=random.Random(20261022)
        pairs.extend((rng.randrange(1,1100),Fraction(rng.randrange(0,33000),10)) for _ in range(240))
        raw=''.join(f'{k} {float(mean):.18g}\n' for k,mean in pairs)
        out=subprocess.check_output([str(self.binary),'probability'],input=raw,text=True).splitlines()
        self.assertEqual(len(out),len(pairs))
        for (margin,mean),line in zip(pairs,out):
            known,units=map(int,line.split());expected=probability(margin,Fraction(mean))
            self.assertEqual(known,expected[0]);self.assertLessEqual(abs(units-expected[1]),1)

    def test_monotonic_probability_and_exact_copy_accounts(self):
        rows=[]
        for delta in (0,1,2,4,8,16,32,100):
            row=self.row();row[19]=delta;rows.append(row)
        out=calculate(self.binary,rows)
        self.assertEqual(out[0][3],0)
        self.assertEqual([r[3] for r in out],sorted(r[3] for r in out))
        self.assertGreater(out[5][3],out[5][9])
        rows=[]
        for margin in (1,16,32,48,64,128,160,176,512,1023):
            row=self.row();row[3]=row[4]=row[5]=1024-margin
            row[19]=min(16,row[3]);rows.append(row)
        out=calculate(self.binary,rows)
        self.assertEqual([r[8] for r in out],sorted((r[8] for r in out),reverse=True))
        self.assertTrue(all(r[8]<SCALE for r in out))
        self.assertGreater(out[6][3],0)
        self.assertEqual(out[6][1],10*SECOND)

    def test_missing_history_and_wrong_generation_do_not_get_forecast_credit(self):
        for changes in ({18:0},{19:0},{20:SECOND//2-1},{20:2*SECOND+1},
                {19:977},{6:9},{11:256},{12:4},{13:1},{10:0}):
            row=self.row()
            for key,value in changes.items():row[key]=value
            out=calculate(self.binary,[row])[0]
            self.assertEqual(out[3],0)

    def test_random_unknown_retention_and_overflow_boundaries(self):
        rng=random.Random(812022);rows=[]
        for _ in range(500):
            row=self.row();row[1]=row[13]=rng.randrange(1,row[2]);row[3]=rng.randrange(1025)
            row[4]=row[5]=row[3];row[19]=rng.randrange(row[3]+1)
            row[18]=rng.randrange(2);row[10]=rng.randrange(2)
            row[16]=rng.randrange(row[2]-row[1]+1);row[14]=rng.randrange(row[16]+1)
            rows.append(row)
        row=self.row();row[1]=row[13]=2**64-2;row[2]=2**64-1;rows.append(row)
        out=calculate(self.binary,rows)
        for row,value in zip(rows,out):
            self.assertTrue(0<=value[3]<=value[2]<=row[1]);self.assertEqual(value[3]+value[4],row[1])
            self.assertTrue(0<=value[8]<=SCALE)

    def test_real_cases_recover_cost_opportunity_but_do_not_invent_retirement_signal(self):
        cases=('burst-stopped','eligible-cost','retire')
        paths={c:ARCHIVE/(c+'-persistent')/c/'zenfs.run.log' for c in cases}
        if not all(p.is_file() for p in paths.values()):self.skipTest('真实归档不可用，不构造假证据')
        results={c:replay_log(self.binary,p) for c,p in paths.items()}
        stopped=results['burst-stopped']['重放'][0]
        self.assertEqual(stopped['新建议Zone'],stopped['原实际Zone'])
        hot=max(stopped['候选'],key=lambda r:r['RC']);self.assertEqual(hot['RR抵扣'],0)
        opportunity=results['eligible-cost']['重放'][0]
        hot=max(opportunity['候选'],key=lambda r:r['RC'])
        self.assertNotEqual(opportunity['新建议Zone'],opportunity['原实际Zone'])
        self.assertEqual(opportunity['新建议Zone'],hot['Zone'])
        # 真实反例必须保留：相同历史没有未来删除信号，本原型仍会搬即将退役的热数据。
        retire=results['retire']['重放'][0]
        hot=max(retire['候选'],key=lambda r:r['RC'])
        self.assertEqual(retire['新建议Zone'],hot['Zone'])
        self.assertIsNone(results['eligible-cost']['新策略真实RR次数'])

    def test_old_headers_unchanged_and_old_auditor_refuses_new_mode(self):
        from analyze_window_credit import audit_scores
        from test_persistent_credit_host import persistent_fixture
        for tag in ('期望RR配置：','期望RR成本：'):
            with self.assertRaises(ValueError):
                audit_scores(persistent_fixture()+[tag+'合成测试'],'window',persistent=True)
        manifest=ARCHIVE/'同库身份.json'
        if not manifest.is_file():self.skipTest('实际旧头身份不可用')
        expected=json.loads(manifest.read_text())
        for name in ('rrzc_persistent_credit_selector.h','rrzc_window_credit_selector.h','rrzc_v1_growth.h'):
            self.assertEqual(hashlib.sha256((ROOT/'model'/name).read_bytes()).hexdigest(),
                expected['plugin/zenfs/fs/'+name])

    def test_patch_roundtrip_and_safety_tail_byte_identical(self):
        built=Path('/home/data/xwz/rrzc-baselines/joint-persistent-credit-v1-20261022-results/build/joint-persistent-credit-v1-20261022')
        if not (built/'fs_zenfs.cc').is_file():self.skipTest('持续读实际构建快照不可用')
        patch=(ROOT/'zenfs-expected-rr.patch').read_text()
        with tempfile.TemporaryDirectory(prefix='rrzc-expected-patch-') as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(built/name,fs/name)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            inc=(fs/'rrzc_direct_zenfs.inc').read_text();cc=(fs/'fs_zenfs.cc').read_text()
            self.assertIn('expected_requested && persistent_credit',cc)
            self.assertIn('ZENFS_RRZC_EXPECTED_RR',cc)
            tail='  const auto reserve=zbd_->GetMigrationReserveObservation();'
            self.assertEqual(inc.split(tail)[1],(built/'rrzc_direct_zenfs.inc').read_text().split(tail)[1])
            prefix='''#include "rrzc_expected_rr_selector.h"
#include "rrzc_stable_credit_selector.h"
#include "rrzc_recent_eta_selector.h"
#include <cstdlib>
#include <cstring>
template<class... T> void Info(int,const char*,T...) {}
int main() {
std::vector<rrzc_simple::Candidate> candidates;
std::map<uint64_t,rrzc_v1::RecentBlock> usable;
rrzc_v1::RecentReport recent;rrzc_v1::GrowthHistory growth_history;
rrzc_retention::Frame retention_current;rrzc_retention::History retention_frozen;
rrzc_window_credit::Store::Snapshot window_frozen;
bool frame_ok=true,window_credit=true,persistent_credit=true,expected_rr=true;
int logger_=0;uint64_t shadow_round=1,simple_budget=0,shadow_horizon=10*rrzc_v1::kSecond;
'''
            body=inc.split('  // 两臂统一记录同一冻结历史；')[0]
            # 临时编译夹具是生成产物，不修改实际ZenFS源文件。
            source=Path(tmp)/'wire.cc';source.write_text(prefix+body+'(void)ranked; } }\n')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'model'),
                str(source),'-o',str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run([str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
                self.assertEqual((fs/name).read_bytes(),(built/name).read_bytes())


if __name__=='__main__':unittest.main()
