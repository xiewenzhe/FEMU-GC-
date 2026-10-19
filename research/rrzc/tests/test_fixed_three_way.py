"""合成账目验证分类和净差闭合；示例不代表真实设备结果。"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_fixed_three_way import file_comparison,compare_three
from analyze_guarded_stream import analyze


def arm(moves):
    rows={name:dict(迁移MiB=size,维护类型=types,实际复制=[]) for name,size,types in moves}
    return dict(锚点维护账目=rows,总维护迁移MiB=sum(r["迁移MiB"] for r in rows.values()))


class ThreeWayTests(unittest.TestCase):
    def test_rr_to_zc_is_not_copy_saving(self):
        r=file_comparison(arm([("热文件",32,{"RR":1})]),arm([("热文件",32,{"ZC":1})]))
        self.assertEqual(r["净迁移变化MiB"],0)
        self.assertEqual(r["分类文件数"],{"RR转为ZC但搬运不变":1})

    def test_opposing_changes_do_not_disappear(self):
        r=file_comparison(arm([("甲",64,{"RR":2}),("乙",16,{"ZC":1})]),
            arm([("甲",32,{"ZC":1}),("乙",48,{"ZC":3})]))
        self.assertEqual((r["少复制MiB"],r["多复制MiB"],r["净迁移变化MiB"]),(32,32,0))

    def test_absent_file_does_not_lose_added_copy(self):
        r=file_comparison(arm([]),arm([("新复制",32,{"ZC":1})]))
        self.assertEqual(r["净迁移变化MiB"],32)

    def test_file_total_must_match_global(self):
        a=arm([]);b=arm([("文件",16,{"ZC":1})]);b["总维护迁移MiB"]=32
        with self.assertRaisesRegex(ValueError,"总账"):
            file_comparison(a,b)

    def test_greedy_protocol_refuses_old_results(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-medium-20261015-greedy";run.mkdir()
            (run/"config.csv").write_text("策略\ngreedy\n")
            with self.assertRaisesRegex(ValueError,"Greedy目录"):
                analyze(run,fixed_greedy=True)

    def test_greedy_protocol_does_not_allow_tail_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"tail-ablation-fixed-20261015-greedy"/"guarded-medium-20261015-greedy"
            run.mkdir(parents=True);(run/"config.csv").write_text("策略\ngreedy\n")
            with self.assertRaisesRegex(ValueError,"Greedy目录"):
                analyze(run,fixed_greedy=True,tail_ablation="bypass")

    def test_comparison_refuses_different_binaries(self):
        common=dict(核验通过=True,负载配置="original",软件身份={"程序":"同一哈希"},
                    完整核心身份={},每线程请求轨迹SHA256={})
        g=dict(copy.deepcopy(common),策略="greedy")
        k=dict(copy.deepcopy(common),消融模式="keep")
        b=dict(copy.deepcopy(common),消融模式="bypass")
        k["软件身份"]={"程序":"不同哈希"}
        with self.assertRaisesRegex(ValueError,"软件身份"):
            compare_three(g,k,b)


if __name__=="__main__":unittest.main()
