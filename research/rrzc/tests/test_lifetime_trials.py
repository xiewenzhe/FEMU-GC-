"""合成跨终点维护核账；这些数值不是设备实验结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_lifetime_trials import complete_account,final_free_mib

class LifetimeTrialsTest(unittest.TestCase):
    def fixture(self):
        def log(t,message):
            return f'2026/10/16-00:{t//60:02d}:{t%60:02d}.000000 7 {message}'
        lines=[log(0,'滚动更新提交：代号=0，经过纳秒=0'),log(10,'合成复制'),
            log(11,'ZC 统计：轮次 1，本轮迁移 16777216 字节，本轮重置 1 个 Zone'),
            log(11,'Zone 重置诊断：Zone=805306368，状态=成功，'),
            log(179,'合成复制'),
            log(181,'ZC 统计：轮次 2，本轮迁移 16777216 字节，本轮重置 1 个 Zone'),
            log(181,'Zone 重置诊断：Zone=1073741824，状态=成功，')]
        copies=[dict(行号=i,文件编号='23',有效字节=str(16<<20)) for i in (2,5)]
        result=dict(全程迁移MiB=32,全程ZC=2,全程RR=0)
        return lines,copies,[],result

    def test_complete_counts_shutdown_copy_and_reset(self):
        r=complete_account(*self.fixture())
        self.assertEqual((r['总迁移MiB'],r['重复迁移MiB'],r['全部IO区Reset']),(32,16,2))
        self.assertEqual(r['最后日志相对业务秒'],181)

    def test_complete_must_match_total(self):
        args=list(self.fixture());args[-1]['全程迁移MiB']=16
        with self.assertRaises(ValueError):complete_account(*args)

    def test_complete_requires_clock(self):
        args=list(self.fixture());args[0]=args[0][1:]
        with self.assertRaises(ValueError):complete_account(*args)

    def report(self):
        cap=524288
        return dict(nr_zones=32,zone_list=[dict(slba=i*cap,cap=cap,wp=i*cap,state='EMPTY') for i in range(32)])

    def test_final_free_excludes_metadata_and_full_sentinel(self):
        r=self.report();r['zone_list'][3].update(state='FULL',wp=2**64-1)
        z=r['zone_list'][4];z.update(state='CLOSED',wp=z['slba']+262144)
        self.assertEqual(final_free_mib(r),27*256+128)

    def test_final_invalid_pointer_rejected(self):
        r=self.report();r['zone_list'][3].update(state='CLOSED',wp=2**64-1)
        with self.assertRaises(ValueError):final_free_mib(r)

    def test_final_unknown_geometry_rejected(self):
        r=self.report();r['zone_list'][3]['cap']=1
        with self.assertRaises(ValueError):final_free_mib(r)

if __name__=='__main__':unittest.main()
