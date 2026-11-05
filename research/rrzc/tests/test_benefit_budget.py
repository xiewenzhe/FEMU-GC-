"""收益预算的实际C++编译、独立精确排序与补丁回退验证；合成输入不是设备结果。"""
from fractions import Fraction
from pathlib import Path
import json
import random
import shutil
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent


class BenefitBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-benefit-budget-')
        cls.work=Path(cls.temp.name)
        for name,source in (('unit','test_benefit_budget.cc'),('replay','net_cost_replay.cc')):
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(HERE/source),'-o',str(cls.work/name)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_cpp_execution_guards(self):
        r=subprocess.run([str(self.work/'unit')],check=True,text=True,capture_output=True)
        self.assertIn('源尾部拒绝边界通过',r.stdout)

    def test_rational_cost_and_budget_beyond_old_cap(self):
        rng=random.Random(20261019);mib=1<<20;sec=10**9;payload=[];expected=[]
        for rid in range(1,101):
            gv=rng.randrange(1,16)*mib
            rows=[[1,gv,256*mib,1,1,1,100,0,0,7,1,0,sec,2**64-1,1,0,0,10*sec,30*sec,gv]]
            best=(Fraction(gv,256*mib-gv),gv,2**64-1,1);chosen=1;credit_by_zone={}
            for zone in range(2,7):
                valid=rng.randrange(16,65)*mib
                eta=rng.randrange(1,11)*sec
                known=rng.randrange(5)!=0
                loss10=rng.randrange(0,16)*mib;loss30=3*loss10
                rows.append([zone,valid,256*mib,1,1,1,4000,960,2880,7,1,96,sec,eta,
                    int(known),loss10,loss30,10*sec,30*sec,max(0,valid-loss10) if known else 0])
                loss=min(valid,(loss10*eta+10*sec-1)//(10*sec))
                credit=(valid-loss)*(10*sec-eta)//(10*sec) if known else 0
                credit_by_zone[zone]=credit
                key=(Fraction(valid-credit,256*mib-valid),valid,eta,zone)
                if credit and (key[0]<best[0] or chosen!=1 and key<best):best=key;chosen=zone
            expected.append((chosen,gv,rows,credit_by_zone))
            payload.append(f'{rid} {len(rows)}');payload.extend(' '.join(map(str,row)) for row in rows)
        text='\n'.join(payload)+'\n'
        result=subprocess.run([str(self.work/'replay'),'--benefit-budget'],input=text,text=True,
            capture_output=True,check=True)
        outputs=[json.loads(s) for s in result.stdout.splitlines()]
        self.assertEqual(len(outputs),100)
        over_cap=0
        for out,(zone,gv,rows,credits) in zip(outputs,expected):
            self.assertEqual(out['建议Zone'],zone)
            if zone==1:continue
            valid=rows[zone-1][1];extra=valid-gv;limit=(credits[zone]*(256*mib-gv)-1)//(256*mib)
            upper=max(r[1]-gv for r in rows)
            self.assertEqual(out['执行预算字节'],min(limit,upper))
            self.assertLessEqual(extra,out['执行预算字节'])
            over_cap+=extra>16*mib
        self.assertGreater(over_cap,0)
        # 默认入口仍使用原16MiB，且不输出新入口的执行预算字段。
        before=subprocess.run([str(self.work/'replay')],input=text,text=True,capture_output=True,check=True)
        for row in map(json.loads,before.stdout.splitlines()):
            self.assertNotIn('执行预算字节',row)
            self.assertLessEqual(row['额外字节'],16*mib)

    def test_patch_is_reversible_and_keeps_safety_path(self):
        patch=(ROOT/'zenfs-benefit-budget.patch').read_text()
        baseline=ROOT.parents[1]/'build-femu/rrzc/aligned-read-work/fs/fs_zenfs.cc'
        if not baseline.is_file():self.skipTest('缺少冻结源码，不能验证实际补丁应用')
        with tempfile.TemporaryDirectory(prefix='rrzc-benefit-patch-') as tmp:
            directory=Path(tmp);target=directory/'fs';target.mkdir()
            shutil.copyfile(baseline,target/'fs_zenfs.cc')
            subprocess.run(['patch','--fuzz=0','-p1','-d',tmp],input=patch,text=True,capture_output=True,check=True)
            text=(target/'fs_zenfs.cc').read_text()
            self.assertIn('simple_budget,execution_budget,tail,target,rechecked',text)
            self.assertIn('shadow_horizon,execution_budget,frame_ok',text)
            self.assertIn('uint64_t execution_budget=shadow_extra_budget;',text)
            self.assertEqual(text.count('rrzc_guard::Apply(decision,proposal,candidates,'),1)
            subprocess.run(['patch','--fuzz=0','-R','-p1','-d',tmp],input=patch,text=True,capture_output=True,check=True)
            self.assertEqual((target/'fs_zenfs.cc').read_bytes(),baseline.read_bytes())


if __name__=='__main__':unittest.main()
