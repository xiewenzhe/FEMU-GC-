"""只测试受控对照分析的拒绝和结论边界；以下构造数据不是实验结果。"""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_joint_opportunity import compare,model_decisions,analyze_all


class JointAnalysisTest(unittest.TestCase):
    def arms(self):
        # 明确为分析器单元测试的示例账目，不写入任何真实实验目录。
        base=dict(场景='测试样例',初态=[dict(RC=928)],请求计划=[('0','0')],ZC次数=1,
                  RR次数=1,总复制字节=9,总成功ZoneReset=2)
        model=copy.deepcopy(base);model.update(RR次数=0,总复制字节=5,总成功ZoneReset=1)
        return base,model

    def test_reports_observed_not_permanent_savings(self):
        result=compare(*self.arms())
        self.assertEqual(result['窗口内RR减少'],1)
        self.assertEqual(result['窗口内复制减少字节'],4)
        self.assertTrue(result['实际结果支持窗口内联合收益'])
        self.assertIn('不能外推',result['边界'])

    def test_negative_copy_result_is_not_successful_saving(self):
        base,model=self.arms();model['总复制字节']=10
        result=compare(base,model)
        self.assertEqual(result['窗口内复制减少字节'],-1)
        self.assertFalse(result['实际结果支持窗口内联合收益'])

    def test_rejects_mismatched_initial_counter(self):
        base,model=self.arms();model['初态'][0]['RC']=929
        with self.assertRaises(ValueError):compare(base,model)

    def test_rejects_different_gc_count_or_trace(self):
        base,model=self.arms();model['ZC次数']=2
        with self.assertRaises(ValueError):compare(base,model)

    def test_actual_choice_must_match_net_cost_ranking(self):
        lines=['统一成本：轮次=1，Zone=1，有效=4，RR抵扣=0，净复制=4，回收=124，安全=1',
               '统一成本：轮次=1，Zone=2，有效=5，RR抵扣=5，净复制=0，回收=123，安全=1',
               '统一决策：轮次=1，实际存在=1，实际Zone=2']
        self.assertEqual(len(model_decisions(lines)),1)
        lines[-1]='统一决策：轮次=1，实际存在=1，实际Zone=1'
        with self.assertRaises(ValueError):model_decisions(lines)
        base,model=self.arms();model['请求计划']=[('0','1')]
        with self.assertRaises(ValueError):compare(base,model)

    def test_complete_audit_requires_all_eight_arms(self):
        def fake_audit(root,mode,scenario):
            base,model=self.arms();result=base if mode=='greedy' else model
            result['场景']=scenario;return result
        with patch('analyze_joint_opportunity.audit',side_effect=fake_audit) as call:
            result=analyze_all(Path('/单元测试示例，不是真实实验'))
            self.assertTrue(result['四类配对验收通过'])
            self.assertEqual(call.call_count,8)
        with patch('analyze_joint_opportunity.audit',side_effect=ValueError('阶段缺失')):
            with self.assertRaises(ValueError):analyze_all(Path('/单元测试示例'))


if __name__=='__main__':unittest.main()
