"""新入口日志的合成回归；示例状态不代表设备结果。"""
import sys
from pathlib import Path
import unittest
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from guarded_rounds import source_check,reserve_check,parse
from analyze_guarded_stream import profile_for,anchor_ledger,timing_for,window_account
from analyze_v1_shadow_stream import SECOND,MAX


def line(marker,row):
    return marker+"，".join(k+"="+str(v) for k,v in row.items())


class GuardedAnalysis(unittest.TestCase):
    def test_medium_plan_is_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            for strategy in ("greedy","guarded"):
                run=Path(directory)/f"guarded-medium-20261015-{strategy}";run.mkdir()
                self.assertEqual(profile_for(run,strategy),("original",656))
                self.assertEqual(timing_for(run,strategy),(900,180))

    def test_old_timing_is_unchanged(self):
        for family in ("stream","cases"):
            self.assertEqual(timing_for(Path(f"guarded-{family}-greedy-20261014"),"greedy"),(180,0))

    def test_medium_cannot_hide_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-medium-20261015-greedy";run.mkdir()
            (run/"victim-profile.csv").touch()
            with self.assertRaises(ValueError):profile_for(run,"greedy")

    def test_arbitrary_medium_duration_label_rejected(self):
        with self.assertRaises(ValueError):timing_for(Path("guarded-medium-1200-greedy"),"greedy")

    def window_fixture(self):
        def log(t,message):
            return f"2026/10/15-00:{t//60:02d}:{t%60:02d}.000000 7 {message}"
        lines=[log(0,"滚动更新提交：代号=0，经过纳秒=0"),log(10,"合成复制"),
            log(11,"ZC 统计：轮次 1，本轮迁移 16777216 字节，本轮重置 1 个 Zone"),
            log(11,"Zone 重置诊断：Zone=805306368，状态=成功，"),log(199,"合成复制"),
            log(200,"RR 统计：尝试 1 次，本次迁移 16777216 字节，本次重置 1 个 Zone，累计迁移 16777216 字节"),
            log(200,"Zone 重置诊断：Zone=1073741824，状态=成功，")]
        copies=[{"行号":n,"文件编号":"23","有效字节":"16777216"} for n in (2,5)]
        operations=[{"代号":"1","操作":"读4KiB","开始纳秒":str(t*10**9),
                     "延迟纳秒":"1000","排程迟到纳秒":"2000"} for t in (100,200)]
        return lines,copies,operations

    def test_window_repeat_keeps_prewarm_identity(self):
        r=window_account(*self.window_fixture(),180,900)
        self.assertEqual((r["ZC次数"],r["RR次数"],r["首次迁移MiB"],r["重复迁移MiB"]),(0,1,0,16))
        self.assertEqual((r["维护源Reset"],r["全部IO区Reset"]),(1,1))

    def test_window_api_excludes_prewarm_and_reports_real_average(self):
        r=window_account(*self.window_fixture(),180,900)
        self.assertEqual(r["业务API耗时"]["读4KiB"]["样本数"],1)
        self.assertEqual(r["业务API耗时"]["读4KiB"]["平均微秒"],1)
        self.assertEqual(r["排程至API完成耗时"]["读4KiB"]["平均微秒"],3)
        self.assertNotIn("写1MiB",r["业务API耗时"])

    def test_window_completion_end_is_exclusive(self):
        r=window_account(*self.window_fixture(),180,200)
        self.assertEqual((r["RR次数"],r["总迁移MiB"],r["全部IO区Reset"]),(0,0,0))

    def test_window_copy_total_must_close(self):
        lines,copies,ops=self.window_fixture();copies[1]["有效字节"]="1"
        with self.assertRaises(ValueError):window_account(lines,copies,ops,180,900)

    def test_window_cannot_merge_threads(self):
        lines,copies,ops=self.window_fixture();lines[4]=lines[4].replace(" 7 "," 8 ")
        with self.assertRaises(ValueError):window_account(lines,copies,ops,180,900)

    def test_window_unknown_clock_rejected(self):
        lines,copies,ops=self.window_fixture();lines[0]=lines[0].replace("滚动更新提交","未知标记")
        with self.assertRaises(ValueError):window_account(lines,copies,ops,180,900)

    def test_original_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-stream-greedy-20261014";run.mkdir()
            self.assertEqual(profile_for(run,"greedy"),("original",656))

    def test_cases_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-cases-guarded-20261014";run.mkdir()
            (run/"victim-profile.csv").write_text("场景配置,热读停止年龄秒,提前退役年龄秒\nmaintenance-cases,65,66\n")
            self.assertEqual(profile_for(run,"guarded"),("maintenance-cases",624))

    def test_profile_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-cases-greedy-20261014";run.mkdir()
            (run/"victim-profile.csv").write_text("场景配置,热读停止年龄秒,提前退役年龄秒\nmaintenance-cases,65,67\n")
            with self.assertRaises(ValueError):profile_for(run,"greedy")

    def test_original_cannot_hide_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/"guarded-stream-greedy-20261014";run.mkdir()
            (run/"victim-profile.csv").touch()
            with self.assertRaises(ValueError):profile_for(run,"greedy")

    def test_unknown_profile_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):profile_for(Path(directory)/"guarded-other-greedy-20261014","greedy")

    def test_anchor_account_follows_file_id(self):
        files=[{"文件名":"/rrzc-stream/g2.hot.anchor.sst","文件编号":"29"}]
        copies=[{"文件编号":"29","有效字节":str(32<<20),"维护类型":"ZC"},
                {"文件编号":"30","有效字节":str(16<<20),"维护类型":"RR"}]
        result=anchor_ledger(files,copies)[files[0]["文件名"]]
        self.assertEqual((result["维护次数"],result["迁移MiB"],result["维护类型"]),(1,32,{"ZC":1}))

    def test_anchor_without_copy_is_zero(self):
        files=[{"文件名":"/rrzc-stream/g2.hot.anchor.sst","文件编号":"29"}]
        self.assertEqual(anchor_ledger(files,[])[files[0]["文件名"]]["迁移MiB"],0)

    def test_anchor_name_reused_cannot_merge(self):
        files=[{"文件名":"/rrzc-stream/g2.hot.anchor.sst","文件编号":str(n)} for n in (29,30)]
        with self.assertRaises(ValueError):anchor_ledger(files,[])

    def test_anchor_duplicate_observation_not_duplicate_copy(self):
        f={"文件名":"/rrzc-stream/g2.hot.anchor.sst","文件编号":"29"}
        c={"文件编号":"29","有效字节":str(32<<20),"维护类型":"ZC"}
        self.assertEqual(anchor_ledger([f,f],[c])[f["文件名"]]["迁移MiB"],32)

    def test_rr_before_candidate_is_not_lost(self):
        c={"文件名":"/rrzc-stream/g0.hot.anchor.sst","文件编号":"23",
           "有效字节":str(32<<20),"维护类型":"RR"}
        self.assertEqual(anchor_ledger([],[c])[c["文件名"]]["迁移MiB"],32)

    def source(self):
        return {"完整":"1","统计准入":"1","经过纳秒":"1000000","当前写指针":"256","原写指针":"256",
                "当前有效字节":"16","原有效字节":"16","稳定可信":"1/1/1","主机代际":"0/0/0","主机序号":"0/0/0","通过":"1"}

    def pool(self):
        r={"轮次":"1","启用":"1","可知":"1","存在":"1","Zone":"805306368","容量字节":str(256<<20),
           "剩余字节":str(64<<20),"有效字节":str(192<<20),"写指针":"1006632960","主机代际":"0","主机序号":"0",
           "复核可知":"1","复核存在":"1","复核稳定":"1"}
        for k in ("Zone","剩余字节","有效字节","写指针","主机代际","主机序号"):r["复核"+k]=r[k]
        return r

    def empty_round(self):
        d={"轮次":"1","真实执行":"0","存在Greedy":"0","GreedyZone":"0","短期建议Zone":"0","原会改选":"0",
           "原额外有效字节":"0","预测达阈值纳秒":str(MAX),"界限纳秒":str(10*SECOND),"预算字节":str(16<<20),
           "尾部允许":"0","尾部分类":"无需约束","复核后可改选":"0","实际改选":"0","实际Zone":"0","实际额外有效字节":"0"}
        old={"轮次":"1","原建议Zone":"0","原会改选":"0","原预测达阈值纳秒":str(MAX),"准入后建议Zone":"0","准入后会改选":"0"}
        return [line("V1增长决策：",old),line("独立约束预留：",self.pool()),line("独立约束决策：",d),
                "简单双因素实际集合：实际选中=0"]

    def test_source_good(self):self.assertTrue(source_check(self.source(),True))

    def test_source_reset_change_rejected(self):
        r=self.source();r["主机代际"]="0/0/1"
        with self.assertRaises(ValueError):source_check(r,True)

    def test_source_timeout_rejected(self):
        r=self.source();r["经过纳秒"]=str(2*SECOND+1)
        with self.assertRaises(ValueError):source_check(r,True)

    def test_source_modified_valid_rejected(self):
        r=self.source();r["当前有效字节"]="15"
        with self.assertRaises(ValueError):source_check(r,True)

    def test_source_unknown_is_not_good(self):
        r=self.source();r["稳定可信"]="1/1/0";r["通过"]="0"
        self.assertFalse(source_check(r,True))

    def test_source_incomplete_cannot_pass(self):
        with self.assertRaises(ValueError):source_check({"完整":"0","通过":"1"},True)

    def test_pool_good(self):self.assertTrue(reserve_check(self.pool()))

    def test_pool_changes_cannot_claim_stable(self):
        for k in ("Zone","剩余字节","有效字节","写指针","主机代际","主机序号"):
            with self.subTest(k=k):
                r=self.pool();r["复核"+k]=str(int(r[k])+1)
                with self.assertRaises(ValueError):reserve_check(r)

    def test_pool_unknown_falls_back(self):
        r=self.pool();r["复核可知"]="0";r["复核稳定"]="0"
        self.assertFalse(reserve_check(r))

    def test_empty_round_closes(self):
        decisions,_,_=parse(self.empty_round(),"greedy")
        self.assertEqual(len(decisions),1)
        self.assertFalse(decisions[0]["独立核验改选"])

    def test_missing_collection_rejected(self):
        with self.assertRaises(ValueError):parse(self.empty_round()[:-1],"greedy")

    def test_wrong_mode_rejected(self):
        with self.assertRaises(ValueError):parse(self.empty_round(),"guarded")

    def test_false_switch_rejected(self):
        rows=self.empty_round();rows[2]=rows[2].replace("实际改选=0","实际改选=1")
        with self.assertRaises(ValueError):parse(rows,"greedy")

    def test_incomplete_new_round_rejected(self):
        with self.assertRaises(ValueError):parse(self.empty_round()+["独立约束源复核：完整=0，通过=0"],"greedy")


if __name__=="__main__":unittest.main()
