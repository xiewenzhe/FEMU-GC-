"""同窗成本核的独立数值、真实日志及旧版本身份回归。"""
import hashlib,json,random,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_joint_time import forecast
from analyze_deadline import forecast as previous

class JointTimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-joint-time-unit-');cls.exe=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_joint_time_selector.cc'),'-o',str(cls.exe)],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_boundaries(self):
        self.assertIn('同窗事件',subprocess.check_output([str(self.exe)],text=True))
    def test_decimal_oracle(self):
        rng=random.Random(2026102207);rows=[];s=10**9
        for _ in range(1200):
            v=rng.randrange(1,134217728);rc=rng.randrange(1,1025)
            d10=rng.randrange(rc+1);d30=rng.randrange(d10,rc+1);g=rng.randrange(d10+1)
            sl=rng.randrange(134217728-v+1);ll=rng.randrange(sl,134217728-v+1)
            rows.append((128,v,134217728,rc,d10,d30,7,100*s,1024,3,rng.randrange(2),128,3,v,
                sl,10*s,ll,30*s,rng.randrange(2),g,rng.choice((s//2,2*s,2500000000,4*s,5*s))))
        raw='\n'.join(' '.join(map(str,r)) for r in rows)+'\n'
        out=[list(map(int,s.split())) for s in subprocess.check_output([str(self.exe),'预测'],input=raw,text=True).splitlines()]
        self.assertEqual(len(out),len(rows))
        for row,a in zip(rows,out):
            e=forecast(row)
            self.assertEqual(a[:2],list(e[:2]));self.assertEqual(a[5:8],list(e[5:8]));self.assertEqual(a[9:11],list(e[9:11]))
            self.assertLessEqual(abs(a[8]-e[8]),1)
            if a[8] and e[8]:self.assertLessEqual(abs(a[11]-e[11]),2)
            if a[11]:
                sl,ss,ll,ls=row[14:18]
                retained=max(0,row[1]-max((sl*a[11]+ss-1)//ss,(ll*a[11]+ls-1)//ls))
                self.assertEqual(a[2],retained)
            elif row[3]<row[8]:self.assertEqual(a[2],0)
            self.assertEqual(a[3],a[2]*a[8]//10**9);self.assertEqual(a[3]+a[4],row[1])
            self.assertLessEqual(a[11],a[10])
    def test_archived_problem_and_nonzero_outside_mean(self):
        s=10**9
        row=(3623878656,19702411,134217728,1002,21,1002,7,100*s,1024,3,1,
            3623878656,3,19702411,0,10357537698,114328204,17357645971,1,3,2357308832)
        old=previous(row);new=forecast(row)
        self.assertGreater(old[8],0);self.assertEqual(old[3],0)
        self.assertEqual(new[8],0);self.assertLess(new[10],3*s)
        no_loss=list(row);no_loss[16]=0
        new=forecast(no_loss);self.assertGreater(new[1],10*s);self.assertGreater(new[3],0)
        self.assertLess(new[11],new[10])
    def test_frozen_old_headers(self):
        p=Path('/home/data/xwz/rrzc-baselines/joint-deadline-v2-20261022-results/同库身份.json')
        if not p.is_file():self.skipTest('缺少真实旧版本身份，不伪造通过')
        old=json.loads(p.read_text())
        for name in ('rrzc_deadline_selector.h','rrzc_v1_growth.h','rrzc_window_credit_selector.h',
                     'rrzc_expected_rr_selector.h','rrzc_space_plan.h'):
            self.assertEqual(hashlib.sha256((ROOT/'model'/name).read_bytes()).hexdigest(),old['plugin/zenfs/fs/'+name])

    def test_real_parent_patch_roundtrip(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-deadline-v2-20261022-results/build/joint-deadline-v2-20261022')
        if not source.is_dir():self.skipTest('缺少真实父源码，不伪造接线通过')
        with tempfile.TemporaryDirectory(prefix='rrzc-joint-time-patch-') as folder:
            p=Path(folder);(p/'fs').mkdir()
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(source/n,p/'fs'/n)
            shutil.copyfile(ROOT/'tests/joint_opportunity_smoke.cc',p/'joint_opportunity_smoke.cc')
            for patch in (ROOT/'zenfs-joint-time.patch',ROOT/'tests/joint-time-client.patch'):
                subprocess.run(['git','apply','--check',str(patch)],cwd=p,check=True)
                subprocess.run(['git','apply',str(patch)],cwd=p,check=True)
            self.assertIn('rrzc_joint_time::Estimate',(p/'fs/rrzc_direct_zenfs.inc').read_text())
            self.assertIn('i+1==JointFillers?JointZone/2:JointZone',(p/'joint_opportunity_smoke.cc').read_text())
            for patch in (ROOT/'tests/joint-time-client.patch',ROOT/'zenfs-joint-time.patch'):
                subprocess.run(['git','apply','--reverse',str(patch)],cwd=p,check=True)
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):self.assertEqual((source/n).read_bytes(),(p/'fs'/n).read_bytes())
            self.assertEqual((ROOT/'tests/joint_opportunity_smoke.cc').read_bytes(),(p/'joint_opportunity_smoke.cc').read_bytes())

    def test_old_auditor_rejects_new_mode(self):
        from analyze_deadline import audit_forecasts
        with self.assertRaisesRegex(ValueError,'旧时序核验入口拒绝'):
            audit_forecasts(['时间一致配置：版本=1，启用=1，旧速率估计不变=1'])

    def test_shell_syntax(self):
        for n in ('build_joint_time_guest.sh','run_joint_time_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)

if __name__=='__main__':unittest.main()
