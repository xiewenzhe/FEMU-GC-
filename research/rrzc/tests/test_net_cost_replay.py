"""编译真实V2头并以独立整数公式核验；合成数据不是设备实验。"""
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from analyze_net_cost_replay import rr_barriers


class NetCostReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='rrzc-net-cost-test-')
        cls.path=Path(cls.directory.name)
        for name,source in (('unit','test_net_cost_selector.cc'),('replay','net_cost_replay.cc')):
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(HERE/source),'-o',str(cls.path/name)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def test_cpp_boundaries(self):
        r=subprocess.run([str(self.path/'unit')],check=True,capture_output=True,text=True,timeout=20)
        self.assertIn('边界通过',r.stdout)

    def test_malformed_input_fails_closed(self):
        for source in ('1 0\n','1 65537\n','1 1\n2 3\n','非整数\n'):
            r=subprocess.run([str(self.path/'replay')],input=source,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(r.returncode,0)

    def test_diagnostic_cap_does_not_replace_cost_or_history(self):
        mib=1<<20;sec=10**9;maximum=2**64-1
        greedy=[1,10*mib,256*mib,1,1,1,100,0,0,7,1,0,sec,maximum,1,0,0,10*sec,30*sec,10*mib]
        other=[2,30*mib,256*mib,1,1,1,4000,960,2880,7,1,96,sec,sec,1,0,0,10*sec,30*sec,30*mib]
        def run(row,*args):
            text='1 2\n'+'\n'.join(' '.join(map(str,r)) for r in (greedy,row))+'\n'
            r=subprocess.run([str(self.path/'replay'),*args],input=text,text=True,capture_output=True,check=True)
            return r.stdout,json.loads(r.stdout)
        before,_=run(other)
        _,diagnostic=run(other,'--diagnose-without-extra-cap')
        self.assertTrue(diagnostic['会改选'])
        self.assertEqual(diagnostic['额外字节'],20*mib)
        self.assertEqual(before,run(other)[0])
        unknown=other.copy();unknown[14]=0;unknown[-1]=0
        self.assertFalse(run(unknown,'--diagnose-without-extra-cap')[1]['会改选'])
        costly=other.copy();costly[13]=10*sec
        self.assertFalse(run(costly,'--diagnose-without-extra-cap')[1]['会改选'])
        bad=subprocess.run([str(self.path/'replay'),'--extra-budget=64'],capture_output=True)
        self.assertNotEqual(bad.returncode,0)

    def test_independent_exact_formula_and_ranking(self):
        rng=random.Random(20261019);mib=1<<20;second=10**9;horizon=10*second
        payload=[];expected=[]
        for rid in range(1,201):
            # Greedy无近期增长；其他区均保持原预算内，只改变可解释的成本因素。
            gv=rng.randrange(8,32)*mib
            rows=[[1,gv,256*mib,1,1,1,100,0,0,7,1,0,second,2**64-1,1,0,0,10*second,30*second,gv]]
            best=(Fraction(gv,256*mib-gv),gv,2**64-1,1);chosen=1
            costs={}
            for zone in range(2,7):
                v=gv+rng.randrange(0,17)*mib
                eta=rng.randrange(0,11)*second
                rc=4096 if eta==0 else 4000
                loss10=rng.randrange(0,24)*mib;loss30=loss10+rng.randrange(0,32)*mib
                rows.append([zone,v,256*mib,1,1,1,rc,960,2880,7,1,96,second,eta,
                             1,loss10,loss30,10*second,30*second,max(0,v-min(loss10,loss30//3))])
                t=max(eta,0 if rc==4096 else second)
                loss=min(v,max((loss10*t+10*second-1)//(10*second),
                               (loss30*t+30*second-1)//(30*second)))
                kept=v-loss;credit=kept*(horizon-t)//horizon;net=v-credit;q=256*mib-v
                costs[zone]=(t,kept,credit,net,q)
                value=(Fraction(net,q),v,t,zone)
                if credit and (value[0]<best[0] or chosen!=1 and value<best):best=value;chosen=zone
            payload.append(f'{rid} {len(rows)}')
            payload.extend(' '.join(map(str,row)) for row in rows)
            expected.append((chosen,costs))
        r=subprocess.run([str(self.path/'replay')],input='\n'.join(payload)+'\n',text=True,
            capture_output=True,check=True,timeout=20)
        outputs=[json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual(len(outputs),200)
        for out,(chosen,costs) in zip(outputs,expected):
            self.assertEqual(out['建议Zone'],chosen)
            self.assertEqual(bool(out['会改选']),chosen!=1)
            self.assertEqual(len(out['成本']),5)
            for c in out['成本']:
                self.assertEqual(tuple(c[k] for k in ('ETA纳秒','预计保留字节','折扣抵扣字节','净复制字节','回收字节')),
                                 costs[c['Zone']])

    def test_rr_classification_is_exclusive_and_not_oracle(self):
        base=dict(序号=1,Zone=2,有效MiB=20,首次正常选区决策之前=False,此前满区观测=None)
        cases=[dict(base,首次正常选区决策之前=True),base,
            dict(base,此前满区观测=dict(回收预算资格=False,有效MiB=100,原比例门槛资格=0)),
            dict(base,此前满区观测=dict(回收预算资格=True,有效MiB=20,原比例门槛资格=1))]
        out=rr_barriers(cases)
        self.assertEqual(sorted(out['分类'].values()),[1,1,1,1])
        self.assertIn('不因观察到未来RR',out['边界'])

    def test_patch_is_scoped_and_reversible(self):
        patch=(ROOT/'zenfs-net-cost-v2.patch').read_text()
        self.assertEqual(patch.count('diff --git '),1)
        additions='\n'.join(s[1:] for s in patch.splitlines() if s.startswith('+') and not s.startswith('+++'))
        for token in ('GC_START_LEVEL','ReadReclaimOneZone','TakeMigrateZone','simple_budget=','shadow_horizon='):
            self.assertNotIn(token,additions)
        self.assertIn('ZENFS_RRZC_NET_COST_MODE',additions)
        baseline=ROOT.parents[1]/'build-femu/rrzc/aligned-read-work/fs/fs_zenfs.cc'
        if not baseline.is_file():self.skipTest('缺少已冻结的当前ZenFS源码，不能伪造应用验证')
        with tempfile.TemporaryDirectory(prefix='rrzc-net-cost-patch-') as directory:
            target=Path(directory)/'fs';target.mkdir()
            shutil.copyfile(baseline,target/'fs_zenfs.cc')
            for extra in ([],['--reverse']):
                subprocess.run(['patch','--fuzz=0','-p1','-d',directory,*extra],
                    input=patch,text=True,capture_output=True,check=True,timeout=10)
            self.assertEqual((target/'fs_zenfs.cc').read_bytes(),baseline.read_bytes())


if __name__=='__main__':unittest.main()
