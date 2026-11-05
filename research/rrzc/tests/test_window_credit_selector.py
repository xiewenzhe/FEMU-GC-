"""窗口模型整数、Reset与读速率边界验证；不把合成样本称为实际收益。"""
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from replay_window_credit import calculate, oracle, retention, first_maintenance, replay_log, SECOND, MAX


class WindowCreditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-window-test-')
        cls.binary=Path(cls.tmp.name)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_window_credit_selector.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_cpp_boundaries(self):
        self.assertIn('不声称真实RR收益',subprocess.check_output([str(self.binary)],text=True))

    def test_random_against_independent_integers(self):
        rng=random.Random(20261021);rows=[]
        for i in range(2400):
            cap=rng.randrange(2,MAX+1);v=rng.randrange(1,cap);rc=rng.randrange(MAX+1)
            d30=rng.randrange(rc+1);d10=rng.randrange(d30+1)
            ss=rng.randrange(1,33*SECOND);ls=rng.randrange(ss,34*SECOND)
            ll=rng.randrange(cap-v+1);sl=rng.randrange(ll+1)
            rows.append((128,v,cap,rc,d10,d30,rng.choice((1,3,7,9,15,17)),
                rng.randrange(1,110*SECOND),rng.choice((0,1024,MAX)),3,
                rng.randrange(2),128,3,v,sl,ss,ll,ls))
        rows.extend([(128,MAX-1,MAX,1,1,1,7,100*SECOND,MAX,3,1,128,3,MAX-1,0,SECOND,0,30*SECOND),
                     (128,MAX-1,MAX,MAX-1,MAX-1,MAX-1,7,100*SECOND,MAX,3,1,128,3,MAX-1,0,SECOND,0,30*SECOND)])
        for _ in range(800):
            v=rng.randrange(1,128*2**20);rc=rng.randrange(1024);d30=rng.randrange(rc+1);d10=rng.randrange(d30+1)
            ls=rng.randrange(SECOND//2,32*SECOND+1);ss=rng.randrange(1,ls+1)
            ll=rng.randrange(128*2**20-v+1);sl=rng.randrange(ll+1)
            rows.append((128,v,128*2**20,rc,d10,d30,7,100*SECOND,1024,3,1,128,3,v,sl,ss,ll,ls))
        self.assertEqual(len(calculate(self.binary,rows)),3202)

    def test_credit_monotonic_and_no_distant_tiny_credit(self):
        base=[128,8*2**20,128*2**20,1000,240,480,7,100*SECOND,1024,3,
              1,128,3,8*2**20,0,2*SECOND,0,2*SECOND]
        rows=[base]
        for change in ({17:10*SECOND,15:10*SECOND},{3:1012},{4:24},{14:16*2**20,16:32*2**20},
                       {4:0,5:0},{4:0,5:720},{10:0}):
            row=base.copy()
            for index,value in change.items():row[index]=value
            rows.append(row)
        out=calculate(self.binary,rows)
        self.assertGreater(out[1][3],out[0][3]);self.assertGreater(out[2][3],out[0][3])
        self.assertEqual(out[3][3],0);self.assertEqual(out[4][3],0)
        self.assertEqual(out[5][3],0);self.assertEqual(out[6][3],0);self.assertEqual(out[7][3],0)
        self.assertTrue(all(0<=r[3]<=r[2]<=base[1] for r in out))

    def test_reset_prefix_requires_full_same_block_count(self):
        row=[128,8*2**20,128*2**20,900,900,900,1,5*SECOND,1024,3,
             1,128,3,8*2**20,0,2*SECOND,0,2*SECOND]
        self.assertGreater(oracle(row)[3],0)
        row[4]=800;self.assertEqual(oracle(row)[0],0)
        row[4]=900;row[7]=10*SECOND;self.assertEqual(oracle(row)[0],0)

    def test_short_retention_unknown_gap_reset_and_migration(self):
        def frame(t,v,known='1'):
            return {'Zone':'128','设备代际':'3','有效字节':str(v),'可知满区':known,
                '时点纳秒':str(t*SECOND),'会话':'1/2','主机代际':'3','主机序号':'6',
                '迁移可信':'1','迁移实例':'1','迁移创建纳秒':'1','迁移前纳秒':str(t*SECOND-1),
                '迁移后纳秒':str(t*SECOND+1),'迁移序号':'0/0','迁移活动':'0/0','迁移末次结束':'0/0'}
        current=frame(100,8)
        f=retention([{128:frame(98,10)},{128:frame(99,9)}],current)
        self.assertEqual(f,[1,128,3,8,2,2*SECOND,2,2*SECOND])
        self.assertEqual(retention([{128:frame(98,10)},{128:frame(99,0,'0')}],current),f)
        self.assertEqual(retention([{128:frame(97,10)}],current)[0],0)
        old=frame(99,9);old['设备代际']='2'
        self.assertEqual(retention([{128:old}],current)[0],0)
        old=frame(99,9);current['迁移序号']='2/2';current['迁移末次结束']=f'{99*SECOND}/{99*SECOND}'
        self.assertEqual(retention([{128:old}],current)[0],0)

    def test_original_future_label_never_crosses_reset(self):
        lines=['d 1 统一决策：轮次=1','d 1 Zone 重置诊断：Zone=128，状态=成功，',
               'd 1 RR 开始：Zone 128，']
        self.assertEqual(first_maintenance(lines,0,128)['事件'],'Reset先发生，停止同代际关联')
        lines=['d 1 统一决策：轮次=1','d 1 RR 开始：Zone 128，']
        self.assertEqual(first_maintenance(lines,0,128)['事件'],'RR先开始')

    def test_actual_controlled_opportunity_and_counterexamples(self):
        root=Path('/home/data/xwz/rrzc-baselines/joint-stable-credit-v1-20261021-results')
        paths=[root/(case+'-'+mode)/case/'zenfs.run.log' for case in
               ('opportunity','retire','stopped','low-yield') for mode in ('greedy','stable')]
        if not all(p.is_file() for p in paths):self.skipTest('实际受控归档不可用，不伪造轨迹')
        reports={}
        for case in ('opportunity','retire','stopped','low-yield'):
            for mode in ('greedy','stable'):
                reports[case,mode]=replay_log(self.binary,root/(case+'-'+mode)/case/'zenfs.run.log',mode)
        self.assertEqual(reports['opportunity','greedy']['改选后原轨迹先发生事件'],{'RR先开始':1})
        for mode in ('greedy','stable'):
            self.assertEqual(reports['stopped',mode]['正抵扣记录数'],0)
            self.assertEqual(reports['low-yield',mode]['相对冻结Greedy改选轮数'],0)
        # 未知未来删除仍是反例；不能偷偷用该实验的删除时刻修正选区输入。
        self.assertGreater(reports['retire','greedy']['正抵扣记录数'],0)
        self.assertEqual(reports['retire','greedy']['改选后原轨迹先发生事件'],
                         {'Reset先发生，停止同代际关联':1})

    def test_patch_roundtrip_and_unchanged_safety_path(self):
        parents={
            'fs_zenfs.cc':Path('/home/data/xwz/rrzc-baselines/joint-rr-fair-ycsb-v1-20261021-results/rr-build/joint-rr-fair-ycsb-v1-20261021-rr-fix/fs_zenfs.cc'),
            'rrzc_direct_zenfs.inc':Path('/home/data/xwz/rrzc-baselines/joint-stable-credit-v1-20261021-results/build/joint-stable-credit-v1-20261021/rrzc_direct_zenfs.inc')}
        if not all(p.is_file() for p in parents.values()):self.skipTest('实际构建归档不可用，不伪造源码')
        patch=(ROOT/'zenfs-window-credit.patch').read_text()
        with tempfile.TemporaryDirectory(prefix='rrzc-window-patch-') as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for n,p in parents.items():shutil.copyfile(p,fs/n)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,
                           check=True,capture_output=True)
            cc=(fs/'fs_zenfs.cc').read_text();inc=(fs/'rrzc_direct_zenfs.inc').read_text()
            self.assertLess(cc.index('window_history.Publish(frame,good);'),
                            cc.index('retention_history.Publish(std::move(frame),good);'))
            self.assertLess(inc.index('std::map<uint64_t,rrzc_window_credit::Retention> window_forecasts;'),
                            inc.index('auto ranked=pair_greedy'))
            tail='  const auto reserve=zbd_->GetMigrationReserveObservation();'
            before=parents['rrzc_direct_zenfs.inc'].read_text().split(tail)[1]
            after=inc.split(tail)[1].replace(
                'window_credit ? window_forecasts.at(c.zone).known : f.known','f.known')
            self.assertEqual(after,before)
            # 编译真实片段中的新旧评分接线，防止补丁可以应用却插进表达式或引用不存在的变量。
            prefix='''#include "rrzc_window_credit_selector.h"
#include "rrzc_stable_credit_selector.h"
#include "rrzc_recent_eta_selector.h"
#include <cstdlib>
#include <cstring>
template<class... T> void Info(int,const char*,T...) {}
int main() {
std::vector<rrzc_simple::Candidate> candidates;
std::map<uint64_t,rrzc_v1::RecentBlock> usable;
rrzc_v1::RecentReport recent;
rrzc_v1::GrowthHistory growth_history;
rrzc_retention::Frame retention_current;
rrzc_retention::History retention_frozen;
rrzc_window_credit::Store::Snapshot window_frozen;
bool frame_ok=true,window_credit=true;
int logger_=0;
uint64_t shadow_round=1,simple_budget=0,shadow_horizon=10*rrzc_v1::kSecond;
'''
            body=inc.split('  // 两臂统一记录同一冻结历史；')[0]
            source=Path(tmp)/'wire.cc';source.write_text(prefix+body+' (void)ranked; } }\n')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'model'),
                str(source),'-o',str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run([str(Path(tmp)/'wire')],check=True,capture_output=True)
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,
                           check=True,capture_output=True)
            for n,p in parents.items():self.assertEqual((fs/n).read_bytes(),p.read_bytes())


if __name__=='__main__':unittest.main()
