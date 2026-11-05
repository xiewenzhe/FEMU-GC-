"""同块阈值标签、删失、时钟对齐及只读采证收尾测试；合成样本不称真实实验。"""
import calendar
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import analyze_expected_rr_probe as audit
import run_expected_rr_probe_host as host


class ExpectedProbeTest(unittest.TestCase):
    def point(self):
        p={k:0 for k in audit.IDENTITY};p.update(设备会话高位=1,Zone=128,设备快照纳秒=100*audit.SECOND,RC=1)
        return p

    def rows(self,values):
        point=self.point();rows=[]
        for time,rc in values:
            rows.append(dict(point,设备快照纳秒=point['设备快照纳秒']+int(time*audit.SECOND),
                RC=rc,窗口标志=7,查询墙钟纳秒=1000*audit.SECOND+int(time*audit.SECOND)))
        return rows

    def outcome(self,values,barriers=()):
        return audit.outcome(self.point(),self.rows(values),8,audit.SECOND,barriers)

    def test_threshold_and_complete_negative_window(self):
        self.assertTrue(self.outcome([(.5,4),(1,8)])['窗口内到阈值'])
        self.assertFalse(self.outcome([(.5,2),(1,3)])['窗口内到阈值'])
        self.assertIsNone(self.outcome([(.5,4),(1.2,8)])['窗口内到阈值'])
        self.assertIsNone(self.outcome([(.5,4)])['窗口内到阈值'])

    def test_reset_identity_change_loss_and_counter_rewind_are_not_negatives(self):
        for change in ({'设备代际':1},{'块编号':1},{'设备会话高位':2},{'代际起点纳秒':1},
                {'通道':1},{'窗口标志':9},{'RC':0}):
            rows=self.rows([(.5,4),(1,8),(1.5,9)])
            rows[0].update(change)
            self.assertIsNone(audit.outcome(self.point(),rows,8,audit.SECOND)['窗口内到阈值'])
        self.assertIsNone(self.outcome([(2,8)])['窗口内到阈值'])

    def test_maintenance_reads_and_retirement_do_not_confirm_natural_prediction(self):
        for reason in ('RR开始','ZC开始','文件退役'):
            barrier=[dict(墙钟纳秒=1000*audit.SECOND+750000000,原因=reason)]
            result=self.outcome([(.5,4),(1,8)],barrier)
            self.assertIsNone(result['窗口内到阈值']);self.assertEqual(result['原因'],reason)
        # 阈值已在维护前被同块观测到，后面的维护不倒扣掉已知标签。
        result=self.outcome([(.5,8)], [dict(墙钟纳秒=1001*audit.SECOND,原因='RR开始')])
        self.assertTrue(result['窗口内到阈值'])

    def test_clock_anchor_derives_offset_instead_of_assuming_utc(self):
        epoch=calendar.timegm(datetime(2026,10,22,3).timetuple())*audit.SECOND
        line=f'2026/10/22-11:00:00.000100 1 块轨迹对时：墙钟纳秒={epoch}'
        self.assertEqual(audit.clock_offset([line]),8*3600*audit.SECOND)
        with self.assertRaises(ValueError):audit.clock_offset([])
        with self.assertRaises(ValueError):audit.clock_offset([line,line])

    def test_observer_compiles_default_off_and_stops_on_both_paths(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-observer-test-') as temp:
            binary=Path(temp)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-pthread',
                '-I'+str(ROOT/'model'),'-I'+str(ROOT/'tests'),str(ROOT/'tests/test_joint_read_trace.cc'),
                '-o',str(binary)],check=True,capture_output=True)
            result=subprocess.check_output([str(binary),temp],text=True)
            self.assertIn('查询失败传播通过',result)
            self.assertIn('完整=1', (Path(temp)/'good/block-trace.status').read_text())
            self.assertIn('完整=0', (Path(temp)/'bad/block-trace.status').read_text())
            frames,count=audit.read_trace(Path(temp)/'good/block-trace.csv',expected_zones=1)
            self.assertGreater(count,0);self.assertEqual(len(frames),1)
            original=(Path(temp)/'good/block-trace.csv').read_text()
            malformed=Path(temp)/'bad.csv';malformed.write_text(original+'1,2\n')
            with self.assertRaises(ValueError):audit.read_trace(malformed,expected_zones=1)

    def test_host_only_enables_observer_for_greedy_business(self):
        from run_persistent_credit_host import PersistentRun
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            run=host.ProbeRun(SimpleNamespace(label='joint-probe-unit'),None,Path(tmp),{})
            with patch.object(PersistentRun,'root',return_value='合成返回') as root:
                command=f'nohup setsid bash {run.target}/run_persistent_credit_guest.sh greedy retire > 日志'
                run.root(command)
                self.assertIn('env RRZC_JOINT_TRACE=1 bash',root.call_args.args[0])
                with self.assertRaises(RuntimeError):run.root(command.replace('greedy retire','persistent retire'))
                run.root('systemctl poweroff')
                self.assertEqual(root.call_args.args[0],'systemctl poweroff')

    def test_guest_scripts_parse_and_observer_has_no_data_read_api(self):
        for name in ('build_persistent_cases_guest.sh','run_persistent_credit_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        header=(ROOT/'tests/joint_read_trace.h').read_text()
        self.assertNotIn('->Read(',header);self.assertNotIn('Reset(',header)
        self.assertNotIn('ZENFS_RRZC_EXPECTED_RR', (ROOT/'tests/run_persistent_credit_guest.sh').read_text())


if __name__=='__main__':unittest.main()
