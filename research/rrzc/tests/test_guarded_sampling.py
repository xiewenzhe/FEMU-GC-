"""合成时间只测试边界复核，不能当作真实采样或收益。"""
import csv
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from guarded_sampling import check_timing,audit_sampling,PERIOD,SECOND


def frames(count=1800):
    return [(i,i*PERIOD+1000000,i*PERIOD+2000000) for i in range(count)]


def boundary():
    result=frames(1799)
    result[-1]=(1798,899*SECOND+6000000,899*SECOND+534000000)
    return result


class GuardedSamplingTests(unittest.TestCase):
    def test_original_exact_count_passes(self):
        self.assertTrue(check_timing(frames(),900,900*SECOND)["原严格批数通过"])

    def test_original_default_still_rejects_missing_batch(self):
        with self.assertRaisesRegex(ValueError,"轨迹缺失"):check_timing(boundary(),900,900*SECOND)

    def test_explicit_final_overrun(self):
        result=check_timing(boundary(),900,900*SECOND+7000000,True)
        self.assertFalse(result["原严格批数通过"])
        self.assertTrue(result["存在末端超时少采"])
        self.assertEqual(result["补造或插值批次数"],0)

    def test_full_count_under_explicit_audit(self):
        self.assertTrue(check_timing(frames(),900,900*SECOND,True)["原严格批数通过"])

    def test_short_original_cannot_use_exception(self):
        with self.assertRaisesRegex(ValueError,"只限"):check_timing(frames(360),180,180*SECOND,True)

    def test_two_missing_batches_rejected(self):
        with self.assertRaisesRegex(ValueError,"只缺末端一个"):check_timing(frames(1798),900,900*SECOND,True)

    def test_serial_gap_rejected(self):
        data=boundary();data[10]=(11,*data[10][1:])
        with self.assertRaisesRegex(ValueError,"连续"):check_timing(data,900,900*SECOND,True)

    def test_internal_delay_is_not_final_boundary(self):
        data=boundary();data[10]=(10,11*PERIOD,11*PERIOD+1)
        with self.assertRaises(ValueError):check_timing(data,900,900*SECOND,True)

    def test_internal_overrun_rejected(self):
        data=boundary();data[10]=(10,10*PERIOD,(11*PERIOD)+1)
        with self.assertRaises(ValueError):check_timing(data,900,900*SECOND,True)

    def test_missing_terminal_without_query_overrun_rejected(self):
        with self.assertRaisesRegex(ValueError,"末查询未超过"):check_timing(frames(1799),900,900*SECOND,True)

    def test_next_query_would_fit_before_stop_rejected(self):
        with self.assertRaisesRegex(ValueError,"仍有采样机会"):check_timing(boundary(),900,901*SECOND,True)

    def test_query_after_stop_rejected(self):
        data=boundary();data[-1]=(1798,899*SECOND,901*SECOND)
        with self.assertRaisesRegex(ValueError,"观察范围"):check_timing(data,900,900*SECOND,True)

    def test_query_time_backwards_rejected(self):
        data=boundary();data[10]=(10,10*PERIOD,10*PERIOD-1)
        with self.assertRaises(ValueError):check_timing(data,900,900*SECOND,True)

    def write_fixture(self,directory,change=None):
        path=Path(directory)
        names=["批次","区索引","查询开始纳秒","查询结束纳秒","设备会话高位","设备会话低位","RR阈值"]
        records=[]
        for i in range(2):
            for zone in range(32):
                records.append(dict(zip(names,(i,zone,i*PERIOD,i*PERIOD+1,1,2,4096))))
        if change:change(records)
        with (path/"recent.csv").open("w") as stream:
            writer=csv.DictWriter(stream,fieldnames=names);writer.writeheader();writer.writerows(records)
        (path/"states.csv").write_text("经过纳秒\n1000000000\n")
        return path

    def test_all_batch_metadata_must_match(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.write_fixture(d,lambda rows:rows[1].update(查询开始纳秒=1,查询结束纳秒=2))
            with self.assertRaisesRegex(ValueError,"元数据不一致"):audit_sampling(path,{}, {0:1,1:1},1)

    def test_partial_batch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.write_fixture(d,lambda rows:rows.pop())
            with self.assertRaisesRegex(ValueError,"完整32区"):audit_sampling(path,{}, {0:1,1:1},1)

    def test_duplicate_zone_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.write_fixture(d,lambda rows:rows[1].update(区索引=0))
            with self.assertRaisesRegex(ValueError,"重复"):audit_sampling(path,{}, {0:1,1:1},1)

    def test_normal_metadata_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.write_fixture(d)
            result=audit_sampling(path,{0:[{"time":0},{"time":PERIOD}]},{0:1,1:1},1)
            self.assertEqual(result["最大设备快照间隔秒"],.5)


if __name__=="__main__":unittest.main()
