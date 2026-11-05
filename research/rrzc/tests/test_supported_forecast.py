"""候选预测核的独立数值、身份边界及真实冻结输入测试；不是设备实验。"""
import hashlib,json,random,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from replay_supported_forecast import calculate,frozen_inputs,live,SECOND

def example():
    b=dict(zone=128,index=1,generation=3,id=2,ch=0,lun=0,plane=0,block=1,
        ns=100*SECOND,epoch=0,rc=1000,d10=160,d30=160,flags=7)
    report=dict(threshold=1024,hi=1,lo=2,blocks=128);history=[]
    for age in (3,2,1):
        p=dict(b,ns=b['ns']-age*SECOND,rc=b['rc']-16*age);history.append(dict(report=dict(report),block=p))
    return dict(candidate=(128,5<<20,128<<20),block=b,report=report,history=history,
        retention=[1,128,3,5<<20,0,10*SECOND,0,30*SECOND])

class SupportedForecastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-supported-test-');cls.exe=Path(cls.tmp.name)/'核验器'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_supported_forecast.cc'),'-o',str(cls.exe)],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_controlled_core(self):
        self.assertIn('持续热读可改选',subprocess.check_output([str(self.exe)],text=True))
        c=example();a=calculate(self.exe,[c])[0]
        self.assertEqual(a[7:11],[16,SECOND,16,SECOND]);self.assertGreater(a[3],4<<20)
        self.assertEqual(a[13],2*SECOND)
        for values in ((16,0),(0,16)):
            r=example();cur=r['block']['rc'];r['history'][-1]['block']['rc']=cur-values[0]
            r['history'][-2]['block']['rc']=cur-sum(values)
            r['history'][0]['block']['rc']=min(r['history'][0]['block']['rc'],cur-sum(values))
            self.assertEqual(calculate(self.exe,[r])[0][3],0)

    def test_identity_reset_missing_and_intermediate_reversal(self):
        cases=[]
        for field in ('generation','id','ch','lun','plane','block','epoch','index'):
            r=example();r['history'][-1]['block'][field]+=1;cases.append(r)
        for field in ('hi','lo','threshold','blocks'):
            r=example();r['history'][-1]['report'][field]+=1;cases.append(r)
        r=example();r['history'][-1]['block']=None;cases.append(r)
        r=example();r['history'][-1]['block']['rc']=1001;cases.append(r)
        r=example();r['history'][-1]['block']['ns']=r['block']['ns'];cases.append(r)
        r=example();r['history'][-1]['block']['flags']|=8;cases.append(r)
        for row in calculate(self.exe,cases):self.assertEqual(row[3],0);self.assertEqual(row[6],0)

    def test_threshold_and_retention_unknown(self):
        r=example();r['block']['rc']=1024;r['history']=[]
        a=calculate(self.exe,[r])[0];self.assertEqual(a[3],r['candidate'][1]);self.assertEqual(a[1],0)
        cases=[]
        for index,value in ((0,0),(1,256),(2,4),(3,1),(5,0),(7,33*SECOND)):
            r=example();r['retention'][index]=value;cases.append(r)
        self.assertTrue(all(a[3]==0 for a in calculate(self.exe,cases)))

    def test_no_finite_all_dead_deadline(self):
        f=[1,128,3,32<<20,100<<20,4*SECOND,100<<20,4*SECOND]
        self.assertGreater(live(32<<20,f,2*SECOND),0)
        values=[live(32<<20,f,n*SECOND) for n in range(11)]
        self.assertEqual(values[0],32<<20);self.assertEqual(values,sorted(values,reverse=True))

    def test_old_auditor_rejects_new_protocol(self):
        from analyze_window_credit import audit_scores
        with self.assertRaisesRegex(ValueError,'旧评分入口拒绝支持预测新核'):
            audit_scores(['支持预测配置：版本=2，启用=1，相邻不重叠区间=1'],'window',
                deadline=True,joint_time=True,terminal=True,terminal_version=2)

    def test_independent_random_numerics(self):
        rng=random.Random(2026102314);cases=[]
        for _ in range(800):
            r=example();b=r['block'];b['rc']=rng.randrange(100,1025)
            earlier=sorted(rng.randrange(b['rc']+1) for _ in range(4));r['history']=[]
            max_delta=b['rc']-earlier[0]
            b['d10']=rng.randrange(max_delta,b['rc']+1);b['d30']=rng.randrange(b['d10'],b['rc']+1)
            for i,rc in enumerate(earlier):
                age=(4-i)*SECOND-rng.randrange(400000000)
                p=dict(b,ns=b['ns']-age,rc=rc,d10=min(b['d10'],rc),d30=min(b['d30'],rc))
                r['history'].append(dict(report=dict(r['report']),block=p))
            valid=rng.randrange(1,128<<20);r['candidate']=(128,valid,128<<20)
            short=rng.randrange(128*2**20-valid+1);long=rng.randrange(short,128*2**20-valid+1)
            r['retention']=[1,128,3,valid,short,rng.randrange(1,11)*SECOND,long,30*SECOND]
            if rng.randrange(5)==0:r['history'][-1]['block']['id']+=1
            cases.append(r)
        self.assertEqual(len(calculate(self.exe,[*cases,*[dict(r,compatible=True) for r in cases]])),1600)

    def test_frozen_inputs_reduce_specific_overestimates_not_all_risk(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-terminal-ycsb-v1-20261023-results')
        if not base.is_dir():self.skipTest('缺真实冻结输入，不伪造通过')
        answers={}
        for mode in ('space','model'):
            p=base/('short-'+mode)/('short-'+mode)/'zenfs.run.log';raw=p.read_bytes()
            cases,_=frozen_inputs(raw.decode().splitlines());keys=list(cases)
            answers[mode]=dict(zip(keys,calculate(self.exe,[cases[k] for k in keys])))
            self.assertEqual(raw,p.read_bytes())
        self.assertLess(answers['model'][3,3758096384][3],4096)
        self.assertEqual(answers['model'][2,3489660928][3],0)
        self.assertGreater(answers['space'][2,3892314112][3],40<<20)

    def test_parent_models_unchanged(self):
        p=Path('/home/data/xwz/rrzc-baselines/joint-terminal-v2-20261023-results/同库身份.json')
        if not p.exists():self.skipTest('缺已验收库身份')
        hashes=json.loads(p.read_text())
        for name in ('rrzc_joint_time_selector.h','rrzc_deadline_selector.h','rrzc_terminal_v2.h',
                     'rrzc_terminal_selector.h','rrzc_window_credit_selector.h'):
            self.assertEqual(hashlib.sha256((ROOT/'model'/name).read_bytes()).hexdigest(),hashes['plugin/zenfs/fs/'+name])

    def test_compatible_scope_preserves_archived_joint_opportunities(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-terminal-v2-20261023-results')
        if not base.is_dir():self.skipTest('缺受控正例，不伪造通过')
        for case in ('stable','aging'):
            path=base/(case+'-joint')/'opportunity/zenfs.run.log';raw=path.read_bytes()
            cases,predictions=frozen_inputs(raw.decode().splitlines())
            positive=[dict(cases[key],compatible=True) for key,p in predictions.items() if int(p['RR复制'])>0]
            self.assertTrue(positive)
            for a in calculate(self.exe,positive):
                self.assertGreaterEqual(a[18],50000000);self.assertEqual(a[13],10*SECOND)
                self.assertGreater(a[3],4<<20)
            self.assertEqual(raw,path.read_bytes())

    def test_opt_in_patch_roundtrip_preserves_safety_and_rr(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-terminal-v2-20261023-results/build/joint-terminal-v2-20261023')
        if not base.is_dir():self.skipTest('缺真实父接线，不伪造接线通过')
        with tempfile.TemporaryDirectory(prefix='rrzc-supported-patch-') as folder:
            target=Path(folder)/'fs';target.mkdir();original={}
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
                original[n]=(base/n).read_bytes();shutil.copyfile(base/n,target/n)
            patch=ROOT/'zenfs-supported-forecast.patch'
            subprocess.run(['git','apply','--check',str(patch)],cwd=folder,check=True)
            subprocess.run(['git','apply',str(patch)],cwd=folder,check=True)
            fs=(target/'fs_zenfs.cc').read_text();core=(target/'rrzc_direct_zenfs.inc').read_text()
            self.assertIn('supported_enabled=supported_env && !strcmp(supported_env,"1")',fs)
            self.assertIn('supported_enabled && (!joint_time_enabled || !expected_rr)',fs)
            self.assertIn('const auto cost=supported_enabled ? rrzc_supported::Estimate',core)
            self.assertIn('rrzc_supported::Live',core)
            self.assertIn('rrzc_supported::Scope::CompatibleRates',core)
            self.assertIn('支持预测配置：版本=2',fs)
            before=original['rrzc_direct_zenfs.inc'].decode()
            a,b='  const auto spare=','  decision={};'
            self.assertEqual(core.split(a,1)[1].split(b,1)[0],before.split(a,1)[1].split(b,1)[0])
            old=original['fs_zenfs.cc'].decode();marker='严格RR挂载需要可写维护能力'
            self.assertEqual(fs.split(marker,1)[1],old.split(marker,1)[1])
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=folder,check=True)
            for n in original:self.assertEqual((target/n).read_bytes(),original[n])

if __name__=='__main__':unittest.main()
