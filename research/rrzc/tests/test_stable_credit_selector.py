"""新模型编译、独立整数核验及真实接线可逆性；合成数据不冒充设备收益。"""
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from replay_stable_credit import calculate, oracle, replay_log, MAX, SECOND


class StableCreditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-stable-test-')
        cls.binary=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_stable_credit_selector.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_cpp_boundaries(self):
        self.assertIn('不声称解决突然退役或真实RR收益',subprocess.check_output([str(self.binary)],text=True))

    def test_random_and_overflow_against_independent_integers(self):
        rng=random.Random(20261021);rows=[]
        for _ in range(600):
            capacity=rng.randrange(2,MAX+1);valid=rng.randrange(1,capacity)
            rc=rng.randrange(MAX+1);d30=rng.randrange(rc+1);d10=rng.randrange(d30+1)
            rows.append((0,valid,capacity,rc,d10,d30,rng.choice((1,3,7,9,15)),100*SECOND,
                rng.choice((0,1024,MAX)),rng.randrange(2),rng.randrange(rc+1),
                rng.choice((SECOND//2,SECOND,2*SECOND,3*SECOND)),rng.randrange(2),
                rng.randrange(valid+1),valid,10*SECOND,30*SECOND))
        rows.extend([(0,MAX-1,MAX,1,1,1,7,100*SECOND,MAX,1,1,2*SECOND,1,MAX,MAX,10*SECOND,30*SECOND),
                     (0,MAX-1,MAX,MAX-1,MAX-1,MAX-1,7,100*SECOND,MAX,1,MAX-1,SECOND,1,0,0,10*SECOND,30*SECOND)])
        self.assertEqual(len(calculate(self.binary,rows)),602)

    def test_eta_credit_monotonic_and_zero_growth(self):
        def row(rc=976,d10=160,delta=16,loss=0):
            return (0,5*2**20,128*2**20,rc,d10,max(d10,480),7,100*SECOND,
                    1024,1,delta,SECOND,1,loss,loss*3,10*SECOND,30*SECOND)
        rows=[row(),row(rc=992),row(d10=80),row(delta=1),row(delta=0),row(loss=20*2**20)]
        results=calculate(self.binary,rows)
        self.assertGreater(results[1][3],results[0][3])
        self.assertLess(results[2][3],results[0][3])
        self.assertLess(results[3][3],results[0][3])
        self.assertEqual(results[4][3],0);self.assertEqual(results[5][3],0)
        self.assertTrue(all(0<=r[3]<=r[2]<=rows[i][1] for i,r in enumerate(results)))

    def test_patch_roundtrip_on_actual_archive(self):
        built=Path('/home/data/xwz/rrzc-baselines/joint-recent-eta-v1-20261020-results/build/joint-recent-eta-v1-20261020')
        if not (built/'snapshot.h').is_file():self.skipTest('真实客机归档不可用，不伪造源码')
        patch=(ROOT/'zenfs-stable-credit.patch').read_text()
        with tempfile.TemporaryDirectory(prefix='rrzc-stable-patch-') as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
                shutil.copyfile(built/name,fs/name)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            changed=(fs/'rrzc_direct_zenfs.inc').read_text()
            self.assertIn('ZENFS_RRZC_STABLE_CREDIT',changed)
            self.assertIn('auto ranked=stable_credit ? rrzc_stable_credit::Rank',changed)
            self.assertIn('稳定收益模式：',changed)
            self.assertIn('稳定收益成本：',changed)
            # 目标适配、源稳定复核、单victim迁移及决策日志逐字节保留。
            tail='  const auto reserve=zbd_->GetMigrationReserveObservation();'
            self.assertEqual(changed.split(tail)[1],(built/'rrzc_direct_zenfs.inc').read_text().split(tail)[1])
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,
                           check=True,capture_output=True)
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
                self.assertEqual((fs/name).read_bytes(),(built/name).read_bytes())

    def test_actual_pressure_replay_does_not_invent_maintenance(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-sst-pressure-overlap-v1-20261020-results/overlap/overlap/zenfs.run.log')
        if not path.is_file():self.skipTest('真实YCSB归档不可用，不伪造轨迹')
        result=replay_log(self.binary,path)
        self.assertEqual(result['合格候选记录数'],27)
        self.assertGreater(result['正抵扣记录数'],0)
        self.assertIsNone(result['新策略真实RR次数']);self.assertIsNone(result['新策略真实迁移字节'])
        # 实际反例：原短窗15.70秒；慢速率与时间折减不能仍全额计入。
        row=next(r for item in result['重放'] if item['轮次']==4 for r in item['候选'] if r['Zone']==402653184)
        self.assertGreater(row['稳定ETA秒'],row['短窗ETA秒'])
        self.assertLess(row['新抵扣字节'],row['预计留存字节'])


if __name__=='__main__':unittest.main()
