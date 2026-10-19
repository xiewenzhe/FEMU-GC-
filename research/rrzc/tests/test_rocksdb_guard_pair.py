"""配对身份、执行隔离及末次空间口径的合成测试，不是设备实验结果。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rocksdb_guard_pair import request_identity,terminal_space,compare,completed_rr_prefix
from analyze_rocksdb_observe import analyze

class GuardPairTest(unittest.TestCase):
    def test_completed_rr_prefix_excludes_later_cost(self):
        lines=['RR 开始：Zone 100，达阈值物理块 1，有效数据 12 字节，封闭剩余容量 0 字节',
            'RR 统计：尝试 1 次，本次迁移 12 字节，本次重置 1 个 Zone，累计迁移 12 字节',
            '合成改选时点',
            'RR 开始：Zone 200，达阈值物理块 1，有效数据 24 字节，封闭剩余容量 0 字节',
            'RR 统计：尝试 2 次，本次迁移 24 字节，本次重置 1 个 Zone，累计迁移 36 字节']
        r=completed_rr_prefix(lines,3)
        self.assertEqual(len(r),1);self.assertEqual(r[0]['实际迁移字节'],12)
        self.assertEqual(r[0]['Zone'],'100')
    def test_incomplete_rr_prefix_cannot_be_called_completed_cost(self):
        with self.assertRaises(ValueError):completed_rr_prefix([
            'RR 开始：Zone 100，达阈值物理块 1，有效数据 12 字节，封闭剩余容量 0 字节'],2)
    def requests(self,origin=1000):
        reads=[dict(序号=str(i),键号='0',版本='0',计划纳秒=str(origin+i*2500000)) for i in range(2)]
        writes=[dict(序号='0',起始键序号='0',键数='64',起始版本='1',计划纳秒=str(origin))]
        return reads,writes
    def arms(self,switches=1):
        r=dict(软件身份={'同库':'哈希'},请求身份={'读':'同读','写':'同写'},实际改选次数=0,
            ZC=3,RR成功=9,总迁移MiB=500,ZC迁移MiB=50,RR迁移MiB=450,
            平均与分位请求封装耗时={},终态IO快照={})
        guarded=copy.deepcopy(r);guarded['实际改选次数']=switches
        return dict(greedy=r,guarded=guarded)
    def zones(self):
        return [dict(批次='10',Zone=str(i),阶段='2',容量字节=str(256<<20),
            剩余字节='0',有效字节=str(1<<20),统计稳定='1') for i in range(29)]
    def test_request_identity_ignores_clock_origin_and_read_versions(self):
        a,b=self.requests(5000);b[0]['开始纳秒']='99999'
        c,d=self.requests(9000);c[0]['版本']='64'
        self.assertEqual(request_identity(a,b),request_identity(c,d))
    def test_changed_request_or_rate_cannot_claim_same_input(self):
        a,b=self.requests();before=request_identity(a,b)
        a[1]['计划纳秒']=str(int(a[1]['计划纳秒'])+1000)
        self.assertNotEqual(before,request_identity(a,b))
        a,b=self.requests();b[0]['起始版本']='2'
        self.assertNotEqual(before,request_identity(a,b))
    def test_comparison_rejects_different_library_and_requests(self):
        for key in ('软件身份','请求身份'):
            arms=self.arms();arms['guarded'][key]['额外']='不同'
            with self.assertRaises(ValueError):compare(arms)
    def test_greedy_must_not_execute_switch(self):
        arms=self.arms();arms['greedy']['实际改选次数']=1
        with self.assertRaises(ValueError):compare(arms)
    def test_no_switch_cannot_claim_strategy_effect(self):
        result=compare(self.arms(0))
        self.assertIn('不能检验改选收益',result['结论'])
    def test_rr_reduction_does_not_hide_higher_migration(self):
        arms=self.arms();arms['guarded']['RR成功']-=1;arms['guarded']['总迁移MiB']+=20
        result=compare(arms)['相对Greedy']
        self.assertEqual((result['RR成功'],result['总迁移MiB']),(-1,20))
    def test_terminal_space_is_io_snapshot_not_allocatable_capacity(self):
        z=self.zones();z[0]['统计稳定']='0';z[0]['剩余字节']=str(2<<20)
        r=terminal_space(z)
        self.assertEqual((r['IO区数'],r['IO区有效MiB'],r['IO区可写剩余MiB'],r['重置观察稳定区数']),(29,29,2,28))
        self.assertIn('不等于',r['边界'])
    def test_incomplete_duplicate_or_wrong_phase_snapshot_rejected(self):
        for z in (self.zones()[:-1],self.zones()[:-1]+[self.zones()[0]]):
            with self.assertRaises(ValueError):terminal_space(z)
        z=self.zones();z[0]['阶段']='1'
        with self.assertRaises(ValueError):terminal_space(z)
    def test_execution_family_requires_explicit_mode_and_no_legacy_flags(self):
        root=Path('rocksdb-anchor-slow-20261016')
        with self.assertRaises(ValueError):analyze(root,{},execution_pair='weighted')
        with self.assertRaises(ValueError):analyze(root,{},execution_pair='guarded',anchor_slow=True)
        with self.assertRaises(FileNotFoundError):analyze(root,{},execution_pair='guarded')

if __name__=='__main__':unittest.main()
