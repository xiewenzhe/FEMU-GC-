"""检查真实接线和跨窗账目；示例仅用于核验分账，不是性能数据。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import overlap_source
from analyze_overlap import split_ledger
from audit_colocation_transactions import audit
from run_integrated_greedy_host import PRIOR

ROOT=Path(__file__).resolve().parents[1]


class OverlapTest(unittest.TestCase):
    def test_actual_deployment_imports(self):
        from run_overlap_host import GUEST_MODULES
        with tempfile.TemporaryDirectory() as tmp:
            for n in GUEST_MODULES:shutil.copyfile(ROOT/'tests'/n,Path(tmp)/n)
            subprocess.run(['python3','-I','-c',
                'import sys;sys.path.insert(0,sys.argv[1]);import build_overlap_guest,reuse_overlap_guest,build_read_cost_guest,build_end_to_end_guest',tmp],check=True)

    def test_retirement_requires_actual_rollback_and_keeps_copy_cost(self):
        # 手工示例只用于审计拒绝条件，不是设备实验记录。
        lines=['受控放置业务开始：示例',
            'T A RR 开始：Zone 1，达阈值物理块 1，有效数据 10 字节',
            'T B V1后续文件退役：文件编号=2，文件名=/x.sst，最后链接已删除，元数据持久化成功',
            'T A V1后续迁移复制：文件编号=2，文件名=/x.sst，源Zone=1，有效字节=10，复制成功=1',
            'T A 公共迁移退役处理：文件编号=2，源Zone=1，已退役=1，未提交映射已撤销=1，目标释放成功=1',
            'T A Zone 重置诊断：Zone=1，状态=成功',
            'T A RR 统计：尝试 1 次，本次迁移 10 字节，本次重置 1 个 Zone，累计迁移 10 字节',
            'T A RR 完成：Zone 1 已重置，累计完成 1 次','受控放置业务截止：示例']
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);log=p/'zenfs.log';log.write_text('\n'.join(lines))
            with self.assertRaises(AssertionError):audit(p)
            r=audit(p,allow_retired_rollback=True)
            self.assertEqual(r['维护账目']['总迁移字节'],10)
            self.assertEqual(r['退役撤销复制字节'],10)
            log.write_text('\n'.join(s for s in lines if '公共迁移退役处理：' not in s))
            with self.assertRaises(AssertionError):audit(p,allow_retired_rollback=True)
            log.write_text('\n'.join(lines).replace('目标释放成功=1','目标释放成功=0'))
            with self.assertRaises(AssertionError):audit(p,allow_retired_rollback=True)
            bad='T A V1后续映射提交：文件编号=2，文件名=/x.sst，成功=1，状态=OK'
            log.write_text('\n'.join(bad if '公共迁移退役处理：' in s else s for s in lines))
            with self.assertRaises(AssertionError):audit(p,allow_retired_rollback=True)

    def test_request_body_preserved_and_no_business_drain(self):
        old=(PRIOR/PRIOR.name.removesuffix('-results')/'校准-db_bench_tool.cc').read_text()
        new=overlap_source.client(old)
        method=new.split('  void RRZCSstLifetime(',1)[1].split('  void RRZCRotating(',1)[0]
        self.assertNotIn('rrzc_front_order::GcTick(',method)
        self.assertIn('if(!settling)return;',method)
        self.assertIn('flush.wait=settling;',method)
        self.assertIn('overlap_boundary(1);settling=true;',method)
        self.assertIn('drain(true);overlap_boundary(2);trace.Finish();',method)
        for line in ('auto status=db_.db->Get(options,key,&value);',
                     'auto status=db_.db->Put(write_options_,key,value);',
                     'trace.Record(true,id,before,after,rrzc_rotating::Now());',
                     'trace.Record(false,id,before,after,rrzc_rotating::Now());'):
            self.assertEqual(new.count(line),old.count(line))
        # 请求生成循环完全保留，只改变循环调用的排空/检查函数行为。
        start='    if(interleaved) {';stop='    } else {'
        self.assertEqual(new.split(start,1)[1].split(stop,1)[0],old.split(start,1)[1].split(stop,1)[0])

    def test_wrapper_clears_gates_and_keeps_safety(self):
        from phase_time_source import wrapper as time_wrapper
        from calibration_source import once
        old=(PRIOR/'源码/run_pool_business_guest.sh').read_text()
        old=once(old,'pool_k=0\nif [[ $mode == blocks ]]; then pool_k=1; fi','pool_k=1')
        new=overlap_source.wrapper(time_wrapper(old))
        self.assertIn('gc_gate=""',new)
        self.assertIn('RRZC_CAL_DRAIN_BACKGROUND=0 RRZC_CAL_SINGLE_BACKGROUND=0',new)
        for s in ('ZENFS_RR_STRICT=1','ZENFS_RR_ENABLE=1','ZENFS_RRZC_CANDIDATE_POOL=original'):
            self.assertIn(s,new)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'wrapper.sh';path.write_text(new)
            subprocess.run(['bash','-n',str(path)],check=True)

    def test_cross_boundary_keeps_copy_and_transaction_counts(self):
        log='\n'.join(['前缀','复制前','复制前2',
            '前后台重叠维护边界：阶段=1，请求=10，单调纳秒=100',
            '复制后','Zone 重置诊断：Zone=1，状态=成功',
            '前后台重叠维护边界：阶段=2，请求=10，单调纳秒=200'])
        # 审计器派生日志比原始多一行，行号统一按该规则构造。
        tx=[dict(类型='ZC',Zone=1,开始行=2,结束行=7,
            复制=[dict(行=4,字段={'有效字节':'10'}),dict(行=6,字段={'有效字节':'20'})])]
        proof=split_ledger(log,tx);parts=proof['按日志提交顺序分账']
        self.assertEqual(parts['业务']['ZC复制字节'],10)
        self.assertEqual(parts['收尾']['ZC复制字节'],20)
        self.assertEqual(parts['收尾']['ZC完成次数'],1)
        self.assertEqual(parts['收尾']['成功Reset'],1)
        self.assertEqual(len(proof['跨窗事务']),1)


if __name__=='__main__':unittest.main()
