"""区分场景与关机收尾回归；合成样本不作为真实设备结果。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import analyze_persistent_credit as analysis
import run_persistent_cases_host as host
import run_rr_fair_long_v2_host as closing
from run_persistent_credit_host import PersistentRun
from test_persistent_credit_host import log


class PersistentCasesTest(unittest.TestCase):
    def test_existing_shutdown_is_reused_and_no_ycsb_capture_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=PersistentRun(SimpleNamespace(label='joint-close-unit'),None,Path(tmp),{})
            self.assertFalse(run.deployed);self.assertIsNone(run.control)
            self.assertEqual(run.finish.__func__,closing.CollectedRun.finish)
            events=[]
            with (patch.object(run,'collect',side_effect=lambda *a:events.append('归档')),
                  patch.object(run,'close_guest',side_effect=lambda:events.append('关机'))):
                run.finish(Path(tmp),'/客机结果')
            self.assertEqual(events,['归档','归档','关机'])
            self.assertTrue(json.loads((Path(tmp)/'阶段归档.json').read_text())['完整'])

    def test_qmp_slow_exit_waits_before_any_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=PersistentRun(SimpleNamespace(label='joint-close-unit'),None,Path(tmp),{})
            run.state.update(当前客机PID=123,当前镜像='/已核验镜像')
            expected=('file='+run.state['当前镜像']+',if=none,aio=native,cache=none,format=qcow2,id=hd0\0').encode()
            with (patch.object(closing,'Path') as path,patch.object(run,'root') as root,
                  patch.object(run,'shutdown',side_effect=RuntimeError('合成QMP等待超时')),
                  patch.object(closing,'wait_gone',return_value=True) as wait):
                path.return_value.exists.return_value=True
                path.return_value.stat.return_value.st_uid=os.getuid()
                path.return_value.read_bytes.return_value=expected
                run.close_guest()
                wait.assert_called_once_with(123,150);root.assert_not_called()

    def test_shutdown_identity_mismatch_does_not_send_poweroff(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=PersistentRun(SimpleNamespace(label='joint-close-unit'),None,Path(tmp),{})
            run.state.update(当前客机PID=123,当前镜像='/预期镜像')
            with patch.object(closing,'Path') as path,patch.object(run,'shutdown') as shutdown:
                path.return_value.exists.return_value=True
                path.return_value.stat.return_value.st_uid=os.getuid()
                path.return_value.read_bytes.return_value='其他镜像\0'.encode()
                with self.assertRaises(RuntimeError):run.close_guest()
                shutdown.assert_not_called()

    def test_archive_failure_still_closes_and_keeps_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=PersistentRun(SimpleNamespace(label='joint-close-unit'),None,Path(tmp),{})
            with (patch.object(run,'collect',side_effect=RuntimeError('合成归档错误')),
                  patch.object(run,'close_guest') as close):
                with self.assertRaises(RuntimeError):run.finish(Path(tmp),'/客机结果')
                close.assert_called_once()
            result=json.loads((Path(tmp)/'阶段归档.json').read_text())
            self.assertFalse(result['完整']);self.assertFalse(result['客机仍存活'])

    def test_build_only_program_and_parent_identity_is_frozen(self):
        self.assertEqual(host.CASES,('burst-stopped','eligible-cost','retire'))
        self.assertEqual(host.MODES,('greedy','window','persistent'))
        self.assertTrue(all(p.is_file() for p in host.FILES))
        for name in ('build_persistent_cases_guest.sh','run_persistent_credit_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        script=(ROOT/'tests/build_persistent_cases_guest.sh').read_text()
        self.assertNotIn('make ',script);self.assertNotIn('git apply',script)
        self.assertIn('parent.software.sha256',script)
        self.assertIn('eligible-cost',script);self.assertIn('burst-stopped',script)

    def test_new_payload_parameters_do_not_change_original_scenarios(self):
        source=(ROOT/'tests/joint_opportunity_smoke.cc').read_text()
        start=source.index('static uint64_t joint_hot_bytes(')
        body=source[start:source.index('static rrzc_v1::RecentReport',start)]
        program='#include <string>\n#include <cassert>\nconstexpr unsigned long long MiB=1ULL<<20;\n'+body+'''
int main() {
assert(joint_hot_bytes("low-yield")==24*MiB);
assert(joint_hot_bytes("retire")==8*MiB);
assert(joint_hot_bytes("stopped")==5*MiB);
assert(joint_hot_bytes("opportunity")==5*MiB);
assert(joint_hot_bytes("burst-stopped")==5*MiB);
assert(joint_hot_bytes("eligible-cost")==10*MiB);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'payload.cc';p.write_text(program)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(p),'-o',str(Path(tmp)/'payload')],check=True,capture_output=True)
            subprocess.run([str(Path(tmp)/'payload')],check=True)

    def case(self,tmp,scenario,mode):
        root=Path(tmp);(root/'zenfs.run.log').write_text('\n'.join([
            'RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1',
            log('V1影子候选',轮次=1,Zone=256,当前RC=928,十秒真实增量=928),
            log('近期ETA对照',轮次=1,Zone=256,近期可知=1,近期增量=0 if scenario=='burst-stopped' else 16,间隔=1000000000)]))
        window=int(mode!='greedy');persistent=int(mode=='persistent')
        (root/'策略.txt').write_text(f'策略={mode}\nGreedy开关={1-window}\n窗口收益开关={window}\n稳定收益开关=0\nRR公平队列=1\n持续读开关={persistent}\n')
        return dict(初态=[dict(源Zone='128'),dict(源Zone='256')],
            实际模型成本及决策=[dict(决策=dict(轮次='1'),成本=[dict(Zone='128',安全='1'),dict(Zone='256',安全='1')])])

    def test_discrimination_gate_requires_two_eligible_safe_sources(self):
        for scenario in ('burst-stopped','eligible-cost'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as tmp:
                actual=self.case(tmp,scenario,'persistent')
                with patch.object(analysis,'audit',return_value=copy.deepcopy(actual)),patch.object(analysis,'audit_scores',return_value=[]):
                    self.assertTrue(analysis.analyze_arm(tmp,'persistent',scenario)['区分场景核验']['双合格安全候选'])
                actual['实际模型成本及决策'][0]['成本'].pop()
                with patch.object(analysis,'audit',return_value=actual),patch.object(analysis,'audit_scores',return_value=[]),self.assertRaises(ValueError):
                    analysis.analyze_arm(tmp,'persistent',scenario)

    def test_recent_zero_is_not_interchangeable_with_unknown_or_old_zero(self):
        for old,new in [('近期可知=1','近期可知=0'),('近期增量=0','近期增量=16'),('十秒真实增量=928','十秒真实增量=0')]:
            with self.subTest(old=old),tempfile.TemporaryDirectory() as tmp:
                actual=self.case(tmp,'burst-stopped','persistent');p=Path(tmp)/'zenfs.run.log'
                p.write_text(p.read_text().replace(old,new))
                with patch.object(analysis,'audit',return_value=actual),patch.object(analysis,'audit_scores',return_value=[]),self.assertRaises(ValueError):
                    analysis.analyze_arm(tmp,'persistent','burst-stopped')

    def test_already_compiled_source_reaudits_without_whole_failed_run_success(self):
        if not host.MANIFEST.is_file():self.skipTest('真实构建归档不可用')
        expected=host.checked_source()
        self.assertIn('librocksdb_debug.a',expected)
        self.assertIn('plugin/zenfs/fs/rrzc_persistent_credit_selector.h',expected)


if __name__=='__main__':unittest.main()
