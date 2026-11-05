"""四组合入口保护检查；这里不将构造数据称为实验结果。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from colocation_choice_source import frontend,selection
from run_colocation_choice_host import wrapper,valid_tag
from analyze_colocation_choice import decisions


class ChoiceTest(unittest.TestCase):
    def test_wrapper_same_sampler_and_original_candidates(self):
        source=wrapper();subprocess.run(['bash','-n'],input=source,text=True,check=True)
        self.assertIn('ZENFS_RRZC_CANDIDATE_POOL=original',source)
        self.assertIn('RRZC_CAL_GREEDY_EXECUTE="$greedy"',source)
        self.assertIn('ZENFS_RRZC_ALL_BLOCKS=1',source)
        self.assertNotIn('ZENFS_RRZC_SPACE_EMERGENCY',source)
        self.assertNotIn('ZENFS_RRZC_V1_SHADOW',source)
        self.assertIn('"$target/colocation_choice" "$scenario" "$result" "$k"',source)

    def test_frozen_source_keeps_qualification_and_original_greedy(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-repeat-order-v6-20261026-results/源码/修复后-fs_zenfs.cc')
        donor=Path('/home/data/xwz/rrzc-baselines/joint-controlled-fragments-repeat-v2-20261028-results/joint-controlled-fragments-repeat-v2-20261028/共同观测-原始选区.inc')
        if not base.is_file():self.skipTest('冻结源缺失')
        old=base.read_text();new=frontend(old)
        anchor='    uint64_t non_free = zbd_->GetUsedSpace()'
        self.assertEqual(old.split(anchor,1)[1],new.split(anchor,1)[1])
        inc=selection(donor.read_text())
        self.assertIn('const auto chosen=rrzc_unit_cost::Select(inputs)',inc)
        self.assertIn('if(greedy_execute)decision=cal_greedy;',inc)
        self.assertIn('row.safe=source && destination;',inc)
        with self.assertRaises(ValueError):selection(inc)

    def test_label_guard(self):
        for tag in ('choice-k0-greedy-r1','choice-k1-model-r2','smoke-k1-greedy-r1'):valid_tag(tag)
        for tag in ('../escape','choice-k2-model-r1','choice-k1-model-r3'):
            with self.assertRaises(ValueError):valid_tag(tag)

    def test_no_candidate_is_valid_but_unpriced_selection_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'zenfs.log'
            text=('受控放置业务开始：\n共同观测实际决策：轮次=1，执行Greedy=1，模型存在=0，'
                '模型Zone=18446744073709551615，Greedy存在=0，GreedyZone=0，实际存在=0，'
                '实际Zone=0，逐块明细关闭=1\n受控放置业务截止：\n')
            path.write_text(text);self.assertEqual(decisions(root)[0]['候选'],[])
            path.write_text(text.replace('实际存在=0','实际存在=1'))
            with self.assertRaises(AssertionError):decisions(root)


if __name__=='__main__':unittest.main()
