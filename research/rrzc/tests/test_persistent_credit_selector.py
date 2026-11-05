"""持续读修正的合成边界、实际旧日志及接线测试；重放不冒充新设备收益。"""
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
from replay_persistent_credit import calculate,oracle,replay_log,SECOND,MAX
from replay_window_credit import oracle as old_oracle

ARCHIVE=Path('/home/data/xwz/rrzc-baselines/joint-window-credit-v2-20261021-results')


class PersistentCreditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-persistent-test-');cls.binary=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_persistent_credit_selector.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_cpp_boundary_and_direct_rank(self):
        self.assertIn('不声称真实RR收益',subprocess.check_output([str(self.binary)],text=True))

    def row(self):
        return [128,5*2**20,128*2**20,975,975,975,7,100*SECOND,1024,3,
            1,128,3,5*2**20,0,10*SECOND,0,30*SECOND,1,16,SECOND]

    def test_credit_never_exceeds_original_and_slower_recent_rate_lowers_credit(self):
        rows=[]
        for delta in (0,1,8,16,32,100,200):
            row=self.row();row[19]=delta;rows.append(row)
        out=calculate(self.binary,rows)
        self.assertEqual(out[0][3],0)
        self.assertEqual([r[3] for r in out],sorted(r[3] for r in out))
        self.assertTrue(all(r[3]<=r[10] for r in out))
        self.assertEqual(out[-1][3],out[-1][10])

    def test_unknown_stopped_loss_reset_prefix_and_invalid_growth(self):
        rows=[self.row()]
        changes=[{18:0},{19:0},{20:SECOND//2-1},{20:2*SECOND+1},
            {19:976},{6:9},{11:256},{12:4},{13:1},
            {14:80*2**20,16:100*2**20},
            {3:973,4:973,5:973,6:1,7:8632425652,19:0,20:1314439530},
            {3:1024,4:1024,5:1024,18:0}]
        for change in changes:
            row=self.row()
            for k,v in change.items():row[k]=v
            rows.append(row)
        out=calculate(self.binary,rows)
        self.assertGreater(out[0][3],0)
        self.assertTrue(all(r[3]==0 for r in out[1:-1]))
        self.assertEqual(out[-1][3],rows[-1][1])

    def test_random_integer_overflow_and_credit_invariants(self):
        rng=random.Random(20261022);rows=[]
        for i in range(1800):
            cap=rng.randrange(2,MAX+1) if i<900 else 128*2**20
            v=rng.randrange(1,cap);rc=rng.randrange(MAX+1) if i<900 else rng.randrange(1024)
            d30=rng.randrange(rc+1);d10=rng.randrange(d30+1)
            ls=rng.randrange(SECOND//2,33*SECOND);ss=rng.randrange(1,ls+1)
            ll=rng.randrange(cap-v+1);sl=rng.randrange(ll+1)
            rows.append((128,v,cap,rc,d10,d30,rng.choice((1,3,7,9,15,17)),
                rng.randrange(1,110*SECOND),rng.choice((0,1024,MAX)),3,
                rng.randrange(2),128,3,v,sl,ss,ll,ls,rng.randrange(2),rng.randrange(rc+1),
                rng.choice((SECOND//2-1,SECOND//2,SECOND,2*SECOND,2*SECOND+1))))
        rows.extend([(128,MAX-1,MAX,MAX-1,MAX-1,MAX-1,7,100*SECOND,MAX,3,
            1,128,3,MAX-1,0,SECOND,0,30*SECOND,1,MAX-1,SECOND),
            (128,MAX-1,MAX,1,1,1,7,100*SECOND,MAX,3,
             1,128,3,MAX-1,0,SECOND,0,30*SECOND,1,1,SECOND)])
        out=calculate(self.binary,rows)
        self.assertEqual(len(out),1802)
        for values,result in zip(rows,out):
            self.assertLessEqual(result[3],old_oracle(values[:18])[3])
            self.assertTrue(0<=result[3]<=result[2]<=values[1])
            self.assertEqual(result[4]+result[3],values[1])

    def test_real_timed_counterexample_stops_burst_credit(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-window-ycsb-timed-v1-20261021-results/short-window/short-window/zenfs.run.log')
        if not path.is_file():self.skipTest('真实计时短对照不可用，不伪造证据')
        result=replay_log(self.binary,path)
        self.assertEqual(result['原正抵扣记录数'],2)
        self.assertEqual(result['修正正抵扣记录数'],0)
        first=result['重放'][0]
        selected=next(c for c in first['候选'] if c['Zone']==3892314112)
        self.assertEqual(selected['近期增量'],0);self.assertEqual(selected['修正抵扣'],0)
        other=next(c for c in first['候选'] if c['Zone']==3355443200)
        self.assertGreater(other['修正ETA'],30*SECOND)
        self.assertEqual(first['新建议Zone'],3221225472)
        self.assertIsNone(result['新策略真实RR次数'])

    def test_real_controlled_opportunity_not_suppressed_and_limits_preserved(self):
        cases=('opportunity','stopped','low-yield','retire')
        paths={c:ARCHIVE/(c+'-window')/c/'zenfs.run.log' for c in cases}
        if not all(p.is_file() for p in paths.values()):self.skipTest('真实受控归档不可用')
        results={c:replay_log(self.binary,p) for c,p in paths.items()}
        self.assertEqual(results['opportunity']['相对同轮Greedy变化轮数'],1)
        self.assertGreater(results['opportunity']['修正正抵扣记录数'],0)
        self.assertEqual(results['stopped']['修正正抵扣记录数'],0)
        self.assertEqual(results['low-yield']['相对同轮Greedy变化轮数'],0)
        # 突然退役仍可误判，不能读取未来删除时刻修正输入。
        self.assertGreater(results['retire']['修正正抵扣记录数'],0)

    def test_patch_roundtrip_and_safety_tail_byte_identical(self):
        built=ARCHIVE/'build/joint-window-credit-v2-20261021'
        if not (built/'fs_zenfs.cc').is_file():self.skipTest('真实构建快照不可用')
        patch=(ROOT/'zenfs-persistent-credit.patch').read_text()
        with tempfile.TemporaryDirectory(prefix='rrzc-persistent-patch-') as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(built/n,fs/n)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            inc=(fs/'rrzc_direct_zenfs.inc').read_text();cc=(fs/'fs_zenfs.cc').read_text()
            self.assertIn('persistent_requested && window_credit',cc)
            self.assertIn('ZENFS_RRZC_PERSISTENT_CREDIT',cc)
            tail='  const auto reserve=zbd_->GetMigrationReserveObservation();'
            self.assertEqual(inc.split(tail)[1],(built/'rrzc_direct_zenfs.inc').read_text().split(tail)[1])
            prefix='''#include "rrzc_persistent_credit_selector.h"
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
bool frame_ok=true,window_credit=true,persistent_credit=true;
int logger_=0;uint64_t shadow_round=1,simple_budget=0,shadow_horizon=10*rrzc_v1::kSecond;
'''
            body=inc.split('  // 两臂统一记录同一冻结历史；')[0]
            source=Path(tmp)/'wire.cc';source.write_text(prefix+body+'(void)ranked; } }\n')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'model'),
                str(source),'-o',str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run([str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):self.assertEqual((fs/n).read_bytes(),(built/n).read_bytes())

    def test_old_window_header_still_matches_actual_deployed_identity(self):
        manifest=ARCHIVE/'同库身份.json'
        if not manifest.is_file():self.skipTest('实际身份清单不可用')
        expected=json.loads(manifest.read_text())['plugin/zenfs/fs/rrzc_window_credit_selector.h']
        self.assertEqual(hashlib.sha256((ROOT/'model/rrzc_window_credit_selector.h').read_bytes()).hexdigest(),expected)


if __name__=='__main__':unittest.main()
