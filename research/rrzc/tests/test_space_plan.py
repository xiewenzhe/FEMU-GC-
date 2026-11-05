"""独立Python整数判据与C++核交叉验证，拒绝旧分析入口误认新模型。"""
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from analyze_space_plan import demand,choose,audit_plans
from analyze_window_credit import audit_scores

class SpacePlanTests(unittest.TestCase):
    def cross_language(self,first_cost=False):
        rng=random.Random(20261022);cases=[]
        for _ in range(1200):
            f=rng.randrange(5000);n=rng.randrange(5000);wait=rng.randrange(3);rows=[];spaces={}
            for i in range(rng.randrange(12)):
                c=rng.randrange(2,1000);v=rng.randrange(1,c);credit=rng.randrange(v+1);safe=rng.randrange(2)
                rows.append(dict(Zone=str(i),有效=str(v),回收=str(c-v),RR抵扣=str(credit),安全=str(safe)))
                spaces[i]=rng.randrange(c+1)
            cases.append((f,n,wait,rows,spaces))
        with tempfile.TemporaryDirectory(prefix='rrzc-space-unit-') as folder:
            exe=Path(folder)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_space_plan.cc'),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
            raw=[]
            for f,n,w,rows,spaces in cases:
                raw.append(f'{f} {n} {w} {len(rows)}')
                raw.extend(' '.join(map(str,(r['Zone'],r['有效'],int(r['有效'])+int(r['回收']),
                    r['RR抵扣'],spaces[int(r['Zone'])],r['安全']))) for r in rows)
            result=subprocess.run([str(exe),'第二版' if first_cost else '交叉验证'],input='\n'.join(raw)+'\n',capture_output=True,text=True,check=True)
        output=iter(result.stdout.splitlines())
        for f,n,w,rows,spaces in cases:
            plans,zone,progress=choose(rows,spaces,demand(f,n,w),first_cost=first_cost)
            found,z,p,size=map(int,next(output).split())
            self.assertEqual((bool(found),z,bool(p),size),(zone is not None,zone or 0,progress,len(plans)))
            for plan in plans:
                actual=list(map(int,next(output).split()))
                self.assertEqual(actual[:8],[plan[k] for k in ('首区','足够','当前复制','当前回收',
                    '未选预计RR复制','未选预计RR空间','预计总复制','计划区数')])
                self.assertEqual(actual[8:],list(map(int,plan['计划区'].split('/'))))
        self.assertEqual(list(output),[])

    def test_cross_language_v1(self):self.cross_language()

    def test_cross_language_v2(self):self.cross_language(True)

    def test_v2_does_not_choose_by_zone_id_when_plans_tie(self):
        rows=[dict(Zone='1',有效='30',回收='98',RR抵扣='0',安全='1'),
              dict(Zone='2',有效='4',回收='124',RR抵扣='0',安全='1')]
        self.assertEqual(choose(rows,{},demand(0,1100,0))[1],1)
        self.assertEqual(choose(rows,{},demand(0,1100,0),first_cost=True)[1],2)

    def test_old_entry_rejects_new(self):
        with self.assertRaisesRegex(ValueError,'旧评分入口拒绝'):
            audit_scores(['空间方案决策：轮次=1，实际存在=0，实际Zone=0，仅进展=0'],'greedy')

    def test_empty_demand_log_and_tamper(self):
        lines=['空间方案需求：轮次=1，可知=1，空闲=21，非空闲=79，分配等待=0，目标字节=20，缺口字节=0，目标百分比=20',
            '空间方案决策：轮次=1，实际存在=0，实际Zone=0，仅进展=0，原因=单元测试']
        self.assertIsNone(audit_plans(lines)[0]['实际Zone'])
        with self.assertRaises(ValueError):audit_plans([lines[0].replace('缺口字节=0','缺口字节=1'),lines[1]])

    def test_patch_context(self):
        patch=(ROOT/'zenfs-space-plan.patch').read_text()
        self.assertIn('if(space_plan)',patch)
        self.assertIn('if(!space_plan)Info(logger_,"统一决策',patch)
        self.assertNotIn('RR 开始：',patch)

    def test_patch_round_trip_on_actual_source(self):
        fs=Path('/home/data/xwz/rrzc-baselines/joint-candidate-pool-v1-20261022-results/build/joint-candidate-pool-v1-20261022/fs_zenfs.after.cc')
        inc=Path('/tmp/rrzc-strict-mod-20261022/fs/rrzc_direct_zenfs.inc')
        if not fs.is_file() or not inc.is_file():self.skipTest('实际源副本缺失，不伪造接线验收')
        with tempfile.TemporaryDirectory(prefix='rrzc-space-patch-') as folder:
            target=Path(folder);(target/'fs').mkdir();paths=((fs,'fs_zenfs.cc'),(inc,'rrzc_direct_zenfs.inc'))
            for p,n in paths:shutil.copyfile(p,target/'fs'/n)
            patch=ROOT/'zenfs-space-plan.patch'
            subprocess.run(['git','apply','--check',str(patch)],cwd=target,check=True)
            subprocess.run(['git','apply',str(patch)],cwd=target,check=True)
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=target,check=True)
            for p,n in paths:self.assertEqual(p.read_bytes(),(target/'fs'/n).read_bytes())

    def test_v2_patch_round_trip_on_actual_source(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-space-plan-v1-20261022-results/build/joint-space-plan-v1-20261022')
        if not source.is_dir():self.skipTest('第一版实际构建副本尚不存在')
        with tempfile.TemporaryDirectory(prefix='rrzc-space-v2-patch-') as folder:
            target=Path(folder);(target/'fs').mkdir()
            names=('fs_zenfs.cc','rrzc_direct_zenfs.inc')
            for n in names:shutil.copyfile(source/n,target/'fs'/n)
            patch=ROOT/'zenfs-space-plan-v2.patch'
            subprocess.run(['git','apply',str(patch)],cwd=target,check=True)
            self.assertIn('Select(inputs,need,true)',(target/'fs/rrzc_direct_zenfs.inc').read_text())
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=target,check=True)
            for n in names:self.assertEqual((source/n).read_bytes(),(target/'fs'/n).read_bytes())

if __name__=='__main__':unittest.main()
