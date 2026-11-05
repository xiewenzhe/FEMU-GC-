"""轮转负载补丁及统计边界测试；合成日志不是实验结果。"""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from dbbench_hotspot_source import patched as hot_patched
from dbbench_rotating_source import patched,patch
from dbbench_rotating_suite import wrapper_source,capture_source,analysis_source,api_histograms,trace


class RotatingTest(unittest.TestCase):
    def test_patch_is_exact_and_preserves_legacy_read_sleep(self):
        native=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-parallel-v2-20261023-results/源码/db_bench_tool.cc')
        if not native.exists():self.skipTest('归档源不存在')
        before=hot_patched(native.read_text());after=patched(before)
        self.assertEqual(before.count('sleep_for(std::chrono::milliseconds(1))'),after.count('sleep_for(std::chrono::milliseconds(1))'))
        self.assertIn('void RRZCRotating',after)
        self.assertNotIn('sleep',after.split('void RRZCRotating',1)[1].split('void ReadWhileWriting',1)[0])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tools').mkdir();(root/'tools/db_bench_tool.cc').write_text(before)
            subprocess.run(['git','apply','-'],cwd=root,input=patch(before),text=True,check=True)
            self.assertEqual((root/'tools/db_bench_tool.cc').read_text(),after)
            subprocess.run(['git','apply','--reverse','-'],cwd=root,input=patch(before),text=True,check=True)
            self.assertEqual((root/'tools/db_bench_tool.cc').read_text(),before)

    def test_shell_and_generated_python(self):
        text=wrapper_source();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('rrzcrotating',text);self.assertNotIn('finish_after_writes=1',text)
        self.assertIn('RRZC_ROTATE_ROUNDS="$rounds"',text)
        self.assertIn('writes=589824',text)
        for source in (capture_source(),analysis_source()):ast.parse(source)

    def test_api_latency_is_real_call_output_not_native_loop(self):
        text=''
        for role in ('READ','WRITE'):
            text+=f'RRZC调用延迟：操作={role}，次数=20，平均纳秒=1234.500000，P99纳秒=2000，P99.9纳秒=3000，P99.99纳秒=4000\n'
        data=api_histograms(text)
        self.assertEqual(data['WRITE']['平均微秒'],1.2345)
        self.assertEqual(data['READ']['P99.99微秒'],4)
        with self.assertRaises(ValueError):api_histograms(text+text)

    def test_batch_trace_rejects_duplicates(self):
        line='RRZC批次完成：操作=READ，批次=0，累计=100，热点起点=0，键校验=123，开始纳秒=10，完成纳秒=20\n'
        self.assertEqual(trace(line)[('READ',0)],(100,0,123))
        with self.assertRaises(ValueError):trace(line+line)

    def test_standalone_capture_understands_rotating_progress(self):
        module=types.ModuleType('独立轮转采样器');exec(capture_source(),module.__dict__)
        state={'进度':{},'错误':[]}
        module.observe_rotating(state,'RRZC批次完成：操作=WRITE，批次=0，累计=24576，热点起点=0\n'.encode(),'run')
        self.assertEqual(state['进度']['run-线程0'],24576)
        module.observe_rotating(state,'RRZC调用延迟：操作=READ，次数=786432，平均纳秒=1\n'.encode(),'run')
        self.assertEqual(state['确认完成读次数'],786432)

    def test_posix_smoke_load_is_not_limited_by_run_write_count(self):
        import inspect
        from run_dbbench_rotating_host import prepare
        text=inspect.getsource(prepare)
        self.assertIn('--benchmarks=fillseq --num=65536 --writes=65536',text)
        self.assertIn('--benchmarks=rrzcrotating --use_existing_db=1',text)


if __name__=='__main__':unittest.main()
