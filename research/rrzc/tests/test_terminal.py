"""独立整数交叉核验；合成案例不是设备收益结果。"""
import hashlib,json,random,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from replay_terminal import choose,terminal_valid
from analyze_space_plan import demand

class TerminalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-terminal-unit-');cls.exe=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_terminal_selector.cc'),'-o',str(cls.exe)],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_cpp_boundaries(self):
        self.assertIn('空间边界债务',subprocess.check_output([str(self.exe)],text=True))
    def test_independent_integer_oracle(self):
        rng=random.Random(2026102309);cases=[]
        for _ in range(1600):
            count=rng.randrange(1,9);rows=[]
            for z in range(count):
                cap=rng.choice((128,1<<20,128<<20,(1<<62)//count));v=rng.randrange(1,cap)
                p=rng.choice((0,10**9,rng.randrange(10**9+1)));retained=rng.randrange(v+1)
                rows.append(dict(z=z+1,cap=cap,v=v,p=p,retained=retained,credit=retained*p//10**9,
                    space=(cap-retained)*p//10**9,safe=rng.randrange(2),known=rng.randrange(2),terminal=rng.randrange(v+1)))
            nonfree=rng.randrange(1,1<<32);free=nonfree*rng.randrange(30)//100;waiters=rng.randrange(2)
            cases.append((free,nonfree,waiters,rows))
        stream=''
        for free,nonfree,waiters,rows in cases:
            stream+=' '.join(map(str,(free,nonfree,waiters,len(rows))))+'\n'
            stream+='\n'.join(' '.join(str(r[k]) for k in ('z','v','cap','retained','p','safe','known','terminal')) for r in rows)+'\n'
        output=iter(subprocess.check_output([str(self.exe),'重算'],input=stream,text=True).splitlines())
        for free,nonfree,waiters,rows in cases:
            expected=choose(rows,demand(free,nonfree,waiters));actual=list(map(int,next(output).split()))
            self.assertEqual(actual,[int(expected[k]) for k in ('found','zone','progress','known','reference')]+[len(expected['plans'])])
            for p in expected['plans']:
                want=[p[k] for k in ('first','space','gap','deferred','score','priced')]+[len(p['completion'])]
                want+=[v for tail in p['completion'] for v in tail]
                self.assertEqual(list(map(int,next(output).split())),want)
        self.assertEqual(list(output),[])
    def test_retention_unknown_and_generation(self):
        r=dict(z=1,v=40,cap=128,generation=3)
        history=dict(zip(('留存可知','留存Zone','留存代际','留存有效','短减少','短间隔','长减少','长间隔'),
            (1,1,3,40,10,10**10,15,3*10**10)))
        self.assertEqual(terminal_valid(r,history),(True,30))
        self.assertEqual(terminal_valid(dict(r,generation=4),history),(False,40))
        self.assertEqual(terminal_valid(r,None),(False,40))
    def test_frozen_parent_headers(self):
        p=Path('/home/data/xwz/rrzc-baselines/joint-time-consistent-v1-20261023-results/同库身份.json')
        if not p.is_file():self.skipTest('缺少真实父版本身份')
        expected=json.loads(p.read_text())
        for name in ('rrzc_joint_time_selector.h','rrzc_deadline_selector.h','rrzc_window_credit_selector.h',
                     'rrzc_expected_rr_selector.h','rrzc_space_plan.h'):
            self.assertEqual(hashlib.sha256((ROOT/'model'/name).read_bytes()).hexdigest(),expected['plugin/zenfs/fs/'+name])

    def test_actual_parent_patch_and_scripts(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-time-consistent-v1-20261023-results/build/joint-time-consistent-v1-20261023')
        if not source.is_dir():self.skipTest('缺少真实父接线源码')
        with tempfile.TemporaryDirectory(prefix='rrzc-terminal-patch-') as folder:
            d=Path(folder)/'fs';d.mkdir()
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copy2(source/name,d/name)
            patch=ROOT/'zenfs-terminal.patch'
            for args in (['--check'],[],['--reverse']):subprocess.run(['git','apply',*args,str(patch)],cwd=folder,check=True)
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):self.assertEqual((d/name).read_bytes(),(source/name).read_bytes())
        for name in ('build_terminal_guest.sh','run_terminal_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)

    def test_old_entry_rejects_terminal(self):
        from analyze_window_credit import audit_scores
        with self.assertRaisesRegex(ValueError,'旧评分入口拒绝边界新核'):
            audit_scores(['边界配置：版本=1，启用=1，'],'window')

    def test_example_execution_audit_and_tamper(self):
        from analyze_terminal import audit
        from analyze_deadline import choose as old_choose
        rows=[dict(z=1,v=4<<20,cap=128<<20,retained=0,p=0,credit=0,space=0,safe=1,known=False,terminal=4<<20),
              dict(z=2,v=8<<20,cap=128<<20,retained=8<<20,p=10**9,credit=8<<20,space=120<<20,safe=1,known=False,terminal=8<<20)]
        n=demand(18,82,0);expected=choose(rows,n);old,z,_=old_choose(rows,n)
        lines=[]
        def emit(tag,**values):
            lines.append(tag+'：'+','.join('='.join((k,str(v))) for k,v in values.items()).replace(',','，'))
        emit('时序方案需求',轮次=1,可知=1,空闲=18,非空闲=82,分配等待=0,目标字节=20,缺口字节=2,目标百分比=20)
        for r in rows:
            emit('V1影子候选',轮次=1,Zone=r['z'],设备代际=0)
            emit('时序成本',轮次=1,Zone=r['z'],有效=r['v'],容量=r['cap'],安全=1,预计留存=r['retained'],
                 概率十亿分数=r['p'],RR复制=r['credit'],RR空间=r['space'])
            emit('边界留存',轮次=1,Zone=r['z'],可知=0,窗口纳秒=10**10,末态有效=r['terminal'])
        for p in old:emit('时序方案候选',轮次=1,**p)
        emit('边界基准',轮次=1,完整定价=int(expected['known']),空间字节=expected['reference'],原选择存在=1,原选择=z)
        for p in expected['plans']:
            emit('边界方案',轮次=1,首区=p['first'],预计空间=p['space'],空间缺口=p['gap'],已定价=int(p['priced']),
                 延期复制=p['deferred'],合计估价=p['score'])
            for z,space,copy,taken,priced in p['completion']:
                emit('边界补足',轮次=1,首区=p['first'],Zone=z,可用空间=space,全份复制=copy,计价空间=taken,计价复制=priced)
        emit('边界方案决策',轮次=1,实际存在=1,实际Zone=expected['zone'],仅进展=0)
        self.assertEqual(audit(lines)[0]['实际Zone'],expected['zone'])
        damaged=[s.replace('目标百分比=20','目标百分比=25') for s in lines]
        with self.assertRaisesRegex(ValueError,'空间需求被改变'):audit(damaged)
        damaged=[s.replace('延期复制=', '错误复制=') for s in lines]
        with self.assertRaisesRegex(ValueError,'独立重算不一致'):audit(damaged)

    def test_v2_rounding_and_independent_oracle(self):
        from replay_terminal import choose_v2
        exe=Path(self.tmp.name)/'v2'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_terminal_v2.cc'),'-o',str(exe)],check=True)
        self.assertIn('一字节改选',subprocess.check_output([str(exe)],text=True))
        rng=random.Random(2026102310);cases=[];stream=''
        for _ in range(1000):
            rows=[]
            for z in range(rng.randrange(1,9)):
                cap=128<<20;v=rng.randrange(1,cap);p=rng.choice((0,10**9,10**9-1,rng.randrange(10**9)))
                retained=rng.randrange(v+1)
                rows.append(dict(z=z+1,v=v,cap=cap,retained=retained,p=p,credit=retained*p//10**9,
                    space=(cap-retained)*p//10**9,safe=rng.randrange(2),known=rng.randrange(2),terminal=rng.randrange(v+1)))
            f,n,w=rng.randrange(30),100,rng.randrange(2);cases.append((f,n,w,rows))
            stream+=' '.join(map(str,(f,n,w,len(rows))))+'\n'
            stream+='\n'.join(' '.join(str(r[k]) for k in ('z','v','cap','retained','p','safe','known','terminal')) for r in rows)+'\n'
        output=iter(subprocess.check_output([str(exe),'重算'],input=stream,text=True).splitlines())
        for f,n,w,rows in cases:
            expected=choose_v2(rows,demand(f,n,w))
            self.assertEqual(list(map(int,next(output).split())),
                [int(expected[k]) for k in ('found','zone','known')]+[len(expected['plans'])])
            for p in expected['plans']:
                self.assertEqual(list(map(int,next(output).split())),[p[k] for k in ('first','rr_rounding','score')])
        self.assertEqual(list(output),[])

if __name__=='__main__':unittest.main()
