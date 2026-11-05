"""长测只延长请求数并继承共享修复；验证固定父镜像、启动路径及失败收尾。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_rr_fair_long_host as host
import run_stable_ycsb_host as ycsb


class LongRunTest(unittest.TestCase):
    def make_run(self, output):
        return host.LongRun(SimpleNamespace(label='joint-unit-long'), None, output, {})

    def test_fixed_passed_parent_and_four_million_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            self.assertEqual(run.target, host.TARGET)
            self.assertEqual(run.parent_image, host.PARENT)
            self.assertEqual(host.PARENT.stem, host.SOURCE.name.removesuffix('-results')+'-build')
            self.assertEqual(host.sizes('long'), (491520, 4000000, 4096, 65536, 30))
            self.assertLess((491520+4000000)*37, 256 << 20)
            self.assertEqual(run.state['模式顺序'], ['long-greedy', 'long-stable'])

    def test_both_long_modes_inherit_fair_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            for mode in ('greedy', 'stable'):
                command = f'nohup setsid bash {run.target}/run_stable_ycsb_guest.sh long-{mode} {mode} 4000000 < /dev/null &'
                with patch.object(ycsb.StableYcsbRun, 'root', return_value='已拦截') as mocked:
                    self.assertEqual(run.root(command), '已拦截')
                    self.assertIn('env ZENFS_RR_FAIR_QUEUE=1 bash', mocked.call_args.args[0])

    def test_arm_reuses_fixed_parent_and_archives_before_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output/'同库身份.json').write_text(json.dumps({'已测试文件': 'abc'}))
            root = output/'long-stable/long-stable'
            root.mkdir(parents=True)
            # arm只创建外层目录；预置结果模拟归档，不执行任何真实客机命令。
            (root/'RR调度.txt').write_text('等待优先开关=1\n')
            (root/'zenfs.run.log').write_text('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1\n')
            run = self.make_run(output)
            run.parent_image = Path('/不存在的新标签-build.qcow2')
            events = []
            result = {n: 0 for n in ('ZC次数', 'RR次数', '总迁移字节', '总成功ZoneReset', '吞吐量')}
            with patch.object(Path, 'mkdir'), patch.object(run, 'start', side_effect=lambda tag: events.append(('启动', run.parent_image))), \
                 patch.object(run, 'ssh', return_value='abc 已测试文件'), patch.object(run, 'root') as launch, \
                 patch.object(run, 'observe'), patch.object(run, 'finish', side_effect=lambda *a: events.append(('归档关机', a))), \
                 patch.object(host, 'analyze_arm', side_effect=lambda *a: events.append(('验收', a)) or result):
                run.arm('long', 'stable')
            self.assertEqual(events[0], ('启动', host.PARENT))
            self.assertEqual([e[0] for e in events], ['启动', '归档关机', '验收'])
            self.assertIn(host.TARGET+'/run_stable_ycsb_guest.sh long-stable stable 4000000', launch.call_args.args[0])
            self.assertEqual(run.state['已完成'], ['long-stable'])

    def test_failure_still_calls_archive_shutdown_and_does_not_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output/'同库身份.json').write_text('{}')
            run = self.make_run(output)
            with patch.object(run, 'start', side_effect=RuntimeError('启动诊断失败')), patch.object(run, 'finish') as finish:
                with self.assertRaisesRegex(RuntimeError, '启动诊断失败'):
                    run.arm('long', 'greedy')
            finish.assert_called_once_with(output/'long-greedy', '/home/femu/rrzc-sst-results/long-greedy')
            self.assertEqual(run.state['已完成'], [])

    def test_source_evidence_still_passes_and_matches_frozen_program(self):
        if not host.MANIFEST.is_file():
            self.skipTest('真实已通过短测证据不可用，不伪造通过')
        expected, pair = host.checked_source()
        self.assertTrue(pair['配对验收通过'])
        self.assertIn(host.TARGET+'/ycsb/ycsb', expected)
        for mode in ('greedy', 'stable'):
            self.assertTrue(pair['模式'][mode]['RR健康']['验收通过'])


if __name__ == '__main__':
    unittest.main()
