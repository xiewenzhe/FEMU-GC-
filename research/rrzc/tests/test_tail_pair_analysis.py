"""合成小配对回归：验证固定读输入以及空间/搬运账目的分离。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_tail_pair import check_reads,compare,MIB,SECOND
from analyze_maintenance_overlap import RR_STATS


class TailPairAnalysis(unittest.TestCase):
    def reads(self):
        return [{"序号":str(i),"计划纳秒":str(i*SECOND//4096),
                 "开始纳秒":str(i*SECOND//4096+1000),"API耗时纳秒":"100000","字节":"4096"}
                for i in range(8)]

    def pair(self):
        common=dict(核验通过=True,固定观察秒数=35,软件身份={"合成源":"同一哈希"},首次选区前布局={"阶段":"合成起点"},全程迁移MiB=288)
        return (dict(common,策略="greedy",终态布局={"空闲字节":str(992*MIB),"满区可回收字节":"0"}),
                dict(common,策略="v1",终态布局={"空闲字节":str(976*MIB),"满区可回收字节":str(16*MIB)}))

    def test_fixed_reads(self):
        check_reads(self.reads(),8)

    def test_rr_statistics_include_cumulative_bytes(self):
        line=f"RR 统计：尝试 1 次，本次迁移 {240*MIB} 字节，本次重置 1 个 Zone，累计迁移 {240*MIB} 字节"
        self.assertEqual(tuple(map(int,RR_STATS.search(line).groups())),(1,240*MIB,1,240*MIB))

    def test_read_count_changed_rejected(self):
        with self.assertRaises(ValueError): check_reads(self.reads(),9)

    def test_read_plan_changed_rejected(self):
        records=self.reads(); records[-1]["计划纳秒"]="1"
        with self.assertRaises(ValueError): check_reads(records,8)

    def test_read_bytes_changed_rejected(self):
        records=self.reads(); records[0]["字节"]="8192"
        with self.assertRaises(ValueError): check_reads(records,8)

    def test_read_timeout_rejected(self):
        records=self.reads(); records[-1]["API耗时纳秒"]=str(2*SECOND)
        with self.assertRaises(ValueError): check_reads(records,8)

    def test_discarded_space_is_not_extra_copy(self):
        result=compare(*self.pair())
        self.assertEqual(result["空闲损失MiB"],16)
        self.assertEqual(result["净迁移增加MiB"],0)

    def test_pair_software_mismatch_rejected(self):
        g,v=self.pair(); v["软件身份"]={"合成源":"不同哈希"}
        with self.assertRaises(ValueError): compare(g,v)

    def test_pair_initial_layout_mismatch_rejected(self):
        g,v=self.pair(); v["首次选区前布局"]={"阶段":"不同合成状态"}
        with self.assertRaises(ValueError): compare(g,v)

    def test_terminal_space_mismatch_rejected(self):
        g,v=self.pair(); v["终态布局"]["满区可回收字节"]="0"
        with self.assertRaises(ValueError): compare(g,v)


if __name__=="__main__": unittest.main()
