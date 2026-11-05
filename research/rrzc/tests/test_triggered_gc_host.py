"""新入口只在真实零缺口闭环通过后启动配对；合成证据不作为设备结果。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import json
from types import SimpleNamespace

import test_all_blocks_dbbench as fixture
from run_triggered_gc_host import boundary_check, boundary_wrapper, preserve_unverified_image


class TriggeredHostTest(unittest.TestCase):
    def lines(self):
        lines = [s.replace('缺口字节=38', '可知=1，缺口字节=0')
                 for s in fixture.DbBenchTest().lines()]
        lines.append('触发后选区约束：轮次=1，GC已触发=1，原始缺口字节=0，评估需求字节=1，零缺口仍选区=1')
        return lines

    def test_boundary_requires_real_cleanup_evidence(self):
        result = boundary_check('\n'.join(self.lines()), '已触发GC边界验证通过')
        self.assertEqual(result['零缺口完成清理轮数'], 1)
        incomplete = [s for s in self.lines() if 'Zone 重置诊断' not in s]
        with self.assertRaisesRegex(ValueError, '缺少选区及复制映射Reset闭环'):
            boundary_check('\n'.join(incomplete), '已触发GC边界验证通过')

    def test_boundary_rejects_rr_or_failed_contents(self):
        with self.assertRaisesRegex(ValueError, '发生RR'):
            boundary_check('\n'.join(self.lines())+'\nRR 开始：', '已触发GC边界验证通过')
        with self.assertRaisesRegex(ValueError, '内容验证'):
            boundary_check('\n'.join(self.lines()), '失败')

    def test_boundary_shell_from_actual_8g_wrapper(self):
        base = (Path(__file__).parent / 'run_all_blocks_dbbench_guest.sh').read_text()
        text = boundary_wrapper(base)
        self.assertIn('8589934592', text)
        self.assertIn('result=$target/boundary', text)
        self.assertNotIn('run_bench --benchmarks=fillseq', text)
        with tempfile.TemporaryDirectory(prefix='rrzc-boundary-wrapper-') as folder:
            script = Path(folder) / 'run.sh'
            script.write_text(text)
            subprocess.run(['bash', '-n', str(script)], check=True)
        with self.assertRaisesRegex(ValueError, '业务阶段边界'):
            boundary_wrapper('')

    def test_unverified_cleanup_retains_image_and_allows_analysis(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-retained-image-') as folder:
            root = Path(folder)
            (root/'阶段归档.json').write_text(json.dumps(dict(完整=True, 客机仍存活=False)))
            (root/'experiment-triggered.tar.gz').write_bytes('本测试仅验证已归档流程'.encode())
            saves = []
            lease = SimpleNamespace(data={'status':'created'}, save=lambda:saves.append(True))
            run = SimpleNamespace(output=root, target='/home/femu/experiment', tag='triggered',
                image_lease=lease, state={'当前客机PID':None}, record=lambda **_:None)
            self.assertTrue(preserve_unverified_image(run, RuntimeError('无法核对本账号进程占用，保留镜像')))
            self.assertEqual(lease.data['status'],'retained-unverified')
            self.assertEqual(len(saves),1)
            run.state['当前客机PID']=123
            self.assertFalse(preserve_unverified_image(run, RuntimeError('无法核对本账号进程占用，保留镜像')))
            self.assertFalse(preserve_unverified_image(run, RuntimeError('归档损坏')))


if __name__ == '__main__':
    unittest.main()
