"""A至F的新接线单元测试；合成轨迹只用于测试，不是性能结果。"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ycsb_af import WORKLOADS, RATIOS, make_properties, properties, check_trace, latencies


def event(op, key=b'', value=0):
    return op.encode()+struct.pack('<I', len(key))+key+struct.pack('<Q', value)


class AfTest(unittest.TestCase):
    def test_standard_semantics(self):
        base = (ROOT/'tests/workload-stable-unlimited.properties').read_text()
        for w, (_, ratios, distribution) in WORKLOADS.items():
            source = '\n'.join(f'{k}={ratios.get(k,"0")}' for k in RATIOS)
            source += '\nrequestdistribution='+distribution+'\n'
            out = properties(make_properties(base, source, w, 6291456, 1200000))
            self.assertEqual(out['rrzc.independent_keys'], 'false')
            self.assertEqual(out['insertorder'], 'hashed')
            self.assertEqual(out['requestdistribution'], distribution)
            self.assertEqual(out['recordcount'], '6291456')
            self.assertEqual(out['limit.ops'], '0')
            self.assertFalse(any(k.startswith('rrzc.read_') or k.startswith('rrzc.update_') for k in out))
            for key in RATIOS:
                self.assertEqual(out[key], ratios.get(key, '0'))

    def trace(self, letter, suffix, operations):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'合成测试轨迹'
            p.write_bytes(event('I', b'a', 1)+event('I', b'b', 2)+event('P', value=2)+suffix)
            return check_trace(p, 2, operations, letter)

    def test_f_logical_and_api_counts(self):
        r = self.trace('f', event('T', value=4)+event('R', b'a')+event('U', b'a', 3)+
                       event('T', value=1)+event('R', b'b'), 2)
        self.assertEqual((r['读取'], r['更新'], r['读改写'], r['业务操作']), (2, 1, 1, 2))

    def test_f_rejects_different_key(self):
        with self.assertRaisesRegex(ValueError, '同一个键'):
            self.trace('f', event('T', value=4)+event('R', b'a')+event('U', b'b', 3), 1)

    def test_e_and_d_insert_counts(self):
        r = self.trace('e', event('T', value=3)+event('S', b'a', 100)+
                       event('T', value=0)+event('I', b'c', 3), 2)
        self.assertEqual((r['扫描'], r['插入'], r['最终键数']), (1, 1, 3))
        self.trace('d', event('T', value=1)+event('R', b'a')+event('T', value=0)+event('I', b'c', 3), 2)

    def test_c_rejects_update(self):
        with self.assertRaises(ValueError):
            self.trace('c', event('T', value=2)+event('U', b'a', 3), 1)

    def test_incomplete_request_rejected(self):
        with self.assertRaises(ValueError):
            self.trace('f', event('T', value=4)+event('R', b'a'), 1)

    def test_latency_f_not_equal_logic_count(self):
        trace = dict(读取=2, 更新=1, 插入=0, 扫描=0, 读改写=1)
        text = '1 sec: 2 operations; '+ ' '.join(
            f'[{op}: Count={n} Avg=1 99=2 99.9=3 99.99=4]' for op, n in
            [('READ', 2), ('UPDATE', 1), ('READMODIFYWRITE', 1)])
        self.assertEqual(len(latencies(text, trace)), 3)
        with self.assertRaises(ValueError):
            latencies(text.replace('READ:', 'READ-FAILED:'), trace)

    def test_shell_and_unchanged_protections(self):
        for name in ('build_ycsb_af_guest.sh', 'run_ycsb_af_guest.sh'):
            subprocess.run(['bash', '-n', str(ROOT/'tests'/name)], check=True)
        script = (ROOT/'tests/run_ycsb_af_guest.sh').read_text()
        for marker in ('34359738368', 'len(z)==256', 'ZENFS_RR_STRICT=1',
                       'ZENFS_RRZC_RECLAIM_BYTES=0', 'ZENFS_RRZC_CANDIDATE_POOL=expanded'):
            self.assertIn(marker, script)
        self.assertNotIn('reset-zone', script)
        old = (ROOT/'manual_zns.py').read_text()
        self.assertIn('memlock < nand', old)
        host = (ROOT/'tests/run_ycsb_af_host.py').read_text()
        self.assertIn('require(args.allow_unlocked', host)
        self.assertIn('resource.setrlimit(resource.RLIMIT_MEMLOCK, (0, 0))', host)

    def test_plot_refuses_missing_real_results(self):
        from plot_ycsb_af import render
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, '尚无正式配对'):
                render(folder)

    def test_parent_is_explicit_not_inherited(self):
        sys.path.insert(0, str(ROOT/'tests'))
        from types import SimpleNamespace
        from run_ycsb_af_host import SuiteRun, PARENT
        with tempfile.TemporaryDirectory() as folder:
            run = SuiteRun(SimpleNamespace(label='joint-af-parent-unit'), None, Path(folder), {})
            self.assertEqual(run.parent_image, PARENT)
            self.assertIn('joint-terminal-v2', str(run.parent_image))

    def test_plot_pipeline_with_explicit_synthetic_fixture(self):
        # 临时合成数据只验证绘图程序，测试结束自动清理，不进入任何实验结果目录。
        from plot_ycsb_af import render
        sample = dict(工作负载='C', 策略名称='合成测试', 总迁移字节=0, RR迁移字节=0,
            ZC迁移字节=0, RR次数=0, ZC次数=0, 总成功ZoneReset=0,
            推导物理Block擦除次数=0, 前台吞吐ops每秒=100, 末态硬件可写字节=1024,
            吞吐量={'平均ops每秒': 90}, 客户端阶段耗时={'前台请求秒': 1, 'Cleanup秒': 0.1},
            API调用延迟={'READ': {'次数': 100, '平均微秒': 1,
                'P99微秒': 2, 'P99.9微秒': 3, 'P99.99微秒': 4}})
        with tempfile.TemporaryDirectory(prefix='rrzc-af-合成绘图测试-') as folder:
            p = Path(folder)
            (p/'full-c-配对核验.json').write_text(json.dumps(dict(配对验收通过=True,
                模式={'space': sample, 'model': sample})))
            out = render(p)
            self.assertTrue((out/'全部柱状图.pdf').is_file())
            self.assertTrue((out/'点读延迟.png').is_file())
            self.assertFalse((out/'更新延迟.png').exists())
            self.assertIn('基数为0，不计算比例', (p/'结果分析.md').read_text())


if __name__ == '__main__':
    unittest.main()
