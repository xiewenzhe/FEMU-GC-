"""用已结束臂的原始日志检查事务解析缺口；篡改副本仅作反例测试。"""
from pathlib import Path
import tempfile
import unittest
from audit_colocation_transactions import audit

BASE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-colocation-cases-v2-20261029-results/逐臂采证')


class TransactionTest(unittest.TestCase):
    def test_real_rr_and_greedy_zc_both_close(self):
        for tag,kind in (('w-first-k0-r1','RR'),('w-first-k1-r1','ZC')):
            if not (BASE/tag/'zenfs.log').is_file():self.skipTest('对应真实日志未归档')
            with self.subTest(tag=tag):
                result=audit(BASE/tag)
                self.assertEqual(len(result['事务']),1)
                self.assertEqual(result['事务'][0]['类型'],kind)

    def test_missing_selection_or_commit_or_reset_cannot_pass_as_empty(self):
        p=BASE/'w-first-k1-r1/zenfs.log'
        if not p.is_file():self.skipTest('ZC真实日志未归档')
        text=p.read_text()
        for marker in ('简单双因素实际集合：','V1后续映射提交：','Zone 重置诊断：'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                (root/'zenfs.log').write_text('\n'.join(line for line in text.splitlines() if marker not in line))
                with self.assertRaises(AssertionError):audit(root)


if __name__=='__main__':unittest.main()
