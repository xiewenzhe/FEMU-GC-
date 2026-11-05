"""编译真实C++实验核；Python精确分数独立验证，合成输入不作为设备收益。"""
from fractions import Fraction
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from replay_total_cost import replay


class TotalCostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-total-')
        cls.binary=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_total_cost_selector.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_cpp_units(self):
        self.assertIn('无法预知退役边界',subprocess.check_output([str(self.binary)],text=True))

    def test_exact_python_oracle(self):
        rng=random.Random(20261020)
        for _ in range(80):
            rows=[]
            for zone in range(rng.randint(1,12)):
                cap=rng.randint(2,2**24);valid=rng.randrange(1,cap);credit=rng.randrange(valid+1)
                rows.append((zone,valid,cap,credit,cap-valid,valid-credit,int(rng.choice((True,True,False)))))
            safe=[r for r in rows if r[6]];pool=sum(r[3] for r in safe)
            expected=sorted(safe,key=lambda r:(Fraction(r[1]+pool-r[3],r[4]),r[1],r[0]))
            raw=str(len(rows))+'\n'+''.join(' '.join(map(str,r))+'\n' for r in rows)
            output=subprocess.check_output([str(self.binary),'replay'],input=raw,text=True).splitlines()
            self.assertEqual(tuple(map(int,output[0].split())),(1,pool,len(safe)))
            self.assertEqual([tuple(map(int,r.split())) for r in output[1:]],
                [(r[0],pool-r[3],r[1]+pool-r[3]) for r in expected])

    def test_replay_does_not_claim_counterfactual_rr_savings(self):
        # 分析器示例输入，只验证重放，不作为设备测量。
        rows=[dict(Zone='1',有效='4',回收='124',RR抵扣='0',净复制='4',安全='1'),
              dict(Zone='2',有效='24',回收='104',RR抵扣='24',净复制='0',安全='1')]
        result=replay(self.binary,[dict(成本=rows,决策=dict(轮次='1',实际Zone='2'))])
        self.assertTrue(result[0]['建议是否变化'])
        self.assertEqual(result[0]['实验核建议Zone'],1)
        self.assertNotIn('实验核真实RR次数',result[0])


if __name__=='__main__':unittest.main()
