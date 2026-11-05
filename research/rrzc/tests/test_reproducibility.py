"""合成输入只验证首差定位与日志损坏检查，不作为实验结果。"""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from analyze_reproducibility import difference, request_rows, request_comparison, compare_stream, transactions, associate_requests,decisions

ROOT=Path(__file__).resolve().parents[1]

class ReproducibilityTest(unittest.TestCase):
    def test_both_greedy_and_model_decision_markers_are_parsed(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for marker in ('统一决策：','时序方案决策：'):
                (root/'run.zenfs.log').write_text('统一成本：轮次=1，Zone=100，有效=7，安全=1\n'+
                    marker+'轮次=1，实际存在=1，实际Zone=100\n')
                rows=decisions(root)
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['选中'],100)
                self.assertTrue(rows[0]['最少有效量选择通过'])

    def test_prefix_difference_is_not_equal(self):
        self.assertEqual(difference([1,2],[1])['首差序号从零起'],1)
        self.assertTrue(difference([],[])['相同'])

    def test_context_preserves_original_records(self):
        a=[dict(内容=x,日志行=10+x) for x in range(5)]
        b=[dict(x) for x in a];b[2]['内容']=99
        result=compare_stream(a,b)
        self.assertEqual(result['首差序号从零起'],2)
        self.assertEqual(result['上下文']['g2'][2]['日志行'],12)

    def test_request_order_compares_key_not_wallclock(self):
        with tempfile.TemporaryDirectory() as name:
            roots=[Path(name)/t for t in ('g1','g2')]
            for shift,root in enumerate(roots):
                root.mkdir()
                (root/'请求提交.bin').write_bytes(b'RRZCREQ1'+struct.pack('<7Q',0,0,0,17,10+shift,20+shift,30+shift))
            self.assertTrue(request_comparison(*roots)['逻辑提交相同'])
            (roots[1]/'请求提交.bin').write_bytes(b'RRZCREQ1'+struct.pack('<7Q',0,0,0,18,10,20,30))
            self.assertEqual(request_comparison(*roots)['首差']['序号'],0)

    def test_request_association_distinguishes_api_and_drain(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'trace.bin'
            p.write_bytes(b'RRZCREQ1'+struct.pack('<7Q',0,0,0,17,10,20,30)+
                struct.pack('<7Q',1,0,1,18,40,50,60))
            events=[dict(单调纳秒估计=n) for n in (5,15,25,45,65)]
            associate_requests(events,p)
            self.assertEqual([e['请求时点关联估计']['位置'] for e in events],
                ['请求提交前','API调用中','API完成后等待放行','API调用中','末请求放行后'])
            self.assertEqual(events[3]['请求时点关联估计']['Key'],18)

    def test_truncated_or_reordered_trace_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'trace'
            for body in (b'bad',b'RRZCREQ1x',b'RRZCREQ1'+struct.pack('<7Q',1,0,0,7,10,20,30),
                         b'RRZCREQ1'+struct.pack('<7Q',0,0,0,7,30,20,10)):
                p.write_bytes(body)
                with self.assertRaises(ValueError):list(request_rows(p))

    def test_maintenance_requires_commit_before_source_reset(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            rows=['RR 开始：Zone 100，达阈值物理块 1，有效数据 7 字节',
                'V1后续迁移复制：文件编号=8，有效字节=7，复制成功=1',
                'V1后续映射提交：文件编号=8，成功=1',
                'Zone 重置诊断：Zone=100，状态=成功',
                'RR 统计：尝试 1 次，本次迁移 7 字节']
            def write():
                (root/'run.zenfs.log').write_text('\n'.join('时点 17 '+x for x in rows))
            write();row=transactions(root)['完成维护'][0]
            self.assertTrue(row['复制到提交再源Reset'])
            self.assertIsNone(row['触发物理块ID'])
            rows[2],rows[3]=rows[3],rows[2];write()
            self.assertFalse(transactions(root)['完成维护'][0]['复制到提交再源Reset'])

    def test_real_two_thread_trace_and_exclusive_creation(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-request-trace-') as name:
            binary=Path(name)/'order';trace=Path(name)/'trace.bin'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'tests'),str(ROOT/'tests/test_front_order.cc'),'-o',str(binary)],check=True)
            env=dict(os.environ,RRZC_CAL_REQUEST_TRACE=str(trace))
            subprocess.run([str(binary)],env=env,check=True,stdout=subprocess.DEVNULL)
            records=list(request_rows(trace))
            self.assertEqual(len(records),7000)
            self.assertEqual([r[1] for r in records[:7]],[0,0,0,0,1,1,1])
            self.assertTrue(all(r[3]==r[2] for r in records))
            # 已存在日志必须拒绝覆盖，错误退出允许；不保留测试core文件。
            def no_core():
                import resource
                resource.setrlimit(resource.RLIMIT_CORE,(0,0))
            failed=subprocess.run([str(binary)],env=env,preexec_fn=no_core,capture_output=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual(len(list(request_rows(trace))),7000)

    def test_device_log_requires_compiled_support_and_restores_environment(self):
        from run_calibration_host import CalibrationRun, ReuseRun
        with tempfile.TemporaryDirectory() as name:
            run=CalibrationRun.__new__(CalibrationRun)
            run.output=Path(name);run.device_event_trace=True;run.record=lambda **kw:None
            argv=['./build-femu/qemu-system-x86_64','-daemonize']
            with patch.object(Path,'read_bytes',return_value='旧二进制'.encode()):
                with self.assertRaisesRegex(ValueError,'尚未编译'):run.command(argv)
            def launch(arguments,timeout):
                self.assertEqual(os.environ['FEMU_RR_EVENT_TRACE'],'1')
                self.assertIn(str(run.output/'FEMU设备事件.log'),arguments)
                raise RuntimeError('合成启动失败')
            with patch.dict(os.environ,{'FEMU_RR_EVENT_TRACE':'原值'}),\
                 patch.object(Path,'read_bytes',return_value=b'FEMU_RR_EVENT_TRACE'),\
                 patch.object(ReuseRun,'command',side_effect=launch):
                with self.assertRaisesRegex(RuntimeError,'合成启动失败'):run.command(argv)
                self.assertEqual(os.environ['FEMU_RR_EVENT_TRACE'],'原值')

if __name__=='__main__':unittest.main()
