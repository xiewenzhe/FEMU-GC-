"""时序方案、独立概率核验和实际源补丁往返；合成输入不冒充设备结果。"""
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from analyze_deadline import choose,forecast,SCALE
from analyze_space_plan import demand
from analyze_window_credit import audit_scores

class DeadlineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='rrzc-deadline-unit-');cls.exe=Path(cls.folder.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_deadline_selector.cc'),'-o',str(cls.exe)],check=True)

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def test_cpp_boundaries(self):
        text=subprocess.check_output([str(self.exe)],text=True)
        self.assertIn('时序替代不重复复制',text)

    def test_integer_cross_check(self):
        rng=random.Random(20261022);cases=[];raw=[]
        for _ in range(1500):
            f,n,w=rng.randrange(5000),rng.randrange(5000),rng.randrange(2);rows=[]
            for z in range(rng.randrange(1,12)):
                cap=rng.randrange(2,1000);v=rng.randrange(1,cap);p=rng.choice([0,SCALE,rng.randrange(SCALE)])
                retained=rng.randrange(v+1);credit=retained*p//SCALE
                rows.append(dict(z=z,v=v,cap=cap,credit=credit,p=p,space=(cap-retained)*p//SCALE,safe=rng.randrange(2)))
            cases.append((rows,demand(f,n,w)));raw.append(f'{f} {n} {w} {len(rows)}')
            raw.extend(' '.join(str(x[k]) for k in ('z','v','cap','credit','p','space','safe')) for x in rows)
        lines=iter(subprocess.check_output([str(self.exe),'方案'],input='\n'.join(raw)+'\n',text=True).splitlines())
        for rows,d in cases:
            plans,z,progress=choose(rows,d);actual=list(map(int,next(lines).split()))
            self.assertEqual(actual,[int(z is not None),z or 0,int(progress),len(plans)])
            for p in plans:
                actual=list(map(int,next(lines).split()))
                self.assertEqual(actual[:8],[p[k] for k in ('首区','足够','预计ZC复制','静态回收','预计RR复制',
                    '预计RR空间','预计总复制','计划区数')])
                self.assertEqual(actual[8:],list(map(int,p['计划区'].split('/'))))
        self.assertEqual(list(lines),[])

    def test_forecast_decimal_oracle(self):
        rng=random.Random(20261023);rows=[];s=10**9
        for _ in range(600):
            z=128;v=rng.randrange(1,1000000);cap=134217728;rc=rng.randrange(1,1025)
            d10=rng.randrange(rc+1);d30=rng.randrange(d10,rc+1);g=rng.randrange(d10+1)
            fk=rng.randrange(2);sl=rng.randrange(min(v,1000));ll=rng.randrange(sl,min(v,1000)+1)
            span=rng.choice((s//2,2*s,2500000000,4*s,5*s))
            rows.append((z,v,cap,rc,d10,d30,7,100*s,1024,3,fk,z,3,v,sl,10*s,ll,30*s,
                rng.randrange(2),g,span))
        actual=[list(map(int,x.split())) for x in subprocess.check_output([str(self.exe),'预测'],
            input='\n'.join(' '.join(map(str,r)) for r in rows)+'\n',text=True).splitlines()]
        for row,out in zip(rows,actual):
            expected=forecast(row)
            self.assertEqual(out[:3],list(expected[:3]));self.assertEqual(out[5:8],list(expected[5:8]))
            self.assertLessEqual(abs(out[8]-expected[8]),1)
            self.assertEqual(out[3],out[2]*out[8]//SCALE);self.assertEqual(out[3]+out[4],row[1])

    def test_legacy_entry_rejects_deadline(self):
        with self.assertRaisesRegex(ValueError,'旧评分入口拒绝时序实验'):
            audit_scores(['时序方案决策：轮次=1，实际存在=0，实际Zone=0，仅进展=0'],'greedy')

    def test_patch_roundtrip_and_scripts(self):
        for n in ('build_deadline_guest.sh','run_deadline_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)
        source=Path('/home/data/xwz/rrzc-baselines/joint-space-plan-v2-20261022-results/build/joint-space-plan-v2-20261022')
        if not source.is_dir():self.skipTest('真实父源不存在，不伪造接线通过')
        with tempfile.TemporaryDirectory(prefix='rrzc-deadline-patch-') as folder:
            target=Path(folder);(target/'fs').mkdir()
            names=('fs_zenfs.cc','rrzc_direct_zenfs.inc')
            for n in names:shutil.copyfile(source/n,target/'fs'/n)
            patch=ROOT/'zenfs-deadline.patch'
            subprocess.run(['git','apply',str(patch)],cwd=target,check=True)
            self.assertIn('rrzc_deadline::Select', (target/'fs/rrzc_direct_zenfs.inc').read_text())
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=target,check=True)
            for n in names:self.assertEqual((source/n).read_bytes(),(target/'fs'/n).read_bytes())

    def test_real_case_cost_order_not_production_forecast(self):
        # 只取已归档首轮实际有效量；RR取确定发生是离线敏感性假设，不是线上预测。
        source=Path('/home/data/xwz/rrzc-baselines/joint-space-plan-v2-20261022-results/short-model/short-model/zenfs.run.log')
        if not source.is_file():self.skipTest('真实日志不存在')
        from analyze_joint_opportunity import fields
        rows=[];d=None
        for line in source.read_text().splitlines():
            if '统一成本：轮次=1，' in line:
                c=fields(line,'统一成本：');z=int(c['Zone']);v=int(c['有效']);p=SCALE if z==4160749568 else 0
                rows.append(dict(z=z,v=v,cap=v+int(c['回收']),credit=v if p else 0,p=p,
                    space=int(c['回收']) if p else 0,safe=int(c['安全'])))
            if '空间方案需求：轮次=1，' in line:
                c=fields(line,'空间方案需求：');d=demand(*(int(c[k]) for k in ('空闲','非空闲','分配等待')))
        plans,zone,_=choose(rows,d)
        self.assertEqual(zone,4160749568)
        winner=next(p for p in plans if p['首区']==zone)
        cold=next(p for p in plans if p['首区']==3892314112)
        self.assertEqual(winner['预计总复制'],cold['预计总复制'])
        self.assertEqual(winner['预计RR复制'],0)
        # 修正改变维护顺序，不把该相同总复制案例包装为减少了41.69MiB。
        self.assertEqual(cold['预计RR复制'],43712524)

if __name__=='__main__':unittest.main()
