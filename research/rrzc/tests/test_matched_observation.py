"""共同观测保留原选区正文；真实双翻译单元验证请求关联与设备摘要。"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from matched_observation_source import direct,backend,frontend,wrapper
from analyze_io_trace import rows,compare

HERE=Path(__file__).resolve().parent
NATIVE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-model-pair-v1-20261028-results/源码/reset-rrzc_direct_zenfs.inc')


class MatchedObservationTest(unittest.TestCase):
    def test_source_keeps_model_and_safety_statements(self):
        if not NATIVE.exists():self.skipTest('实际部署选区片段未归档')
        source=NATIVE.read_text();changed=direct(source)
        self.assertEqual(source.count('rrzc_deadline::Select('),changed.count('rrzc_deadline::Select('))
        self.assertEqual(source.count('row.safe=source && destination;'),changed.count('row.safe=source && destination;'))
        self.assertIn('if(!cal_common)for(const auto& item:proof.distribution)',changed)
        self.assertIn('if(greedy_execute)decision=cal_greedy;',changed)
        with self.assertRaises(ValueError):direct(changed)

    def test_frontend_and_backend_only_add_diagnostic_calls(self):
        source=(HERE/'rrzc_front_order.h').read_text();changed=frontend(source)
        self.assertEqual(changed.count('rrzc_io_trace::Begin('),1)
        self.assertEqual(changed.count('rrzc_io_trace::End('),1)
        self.assertEqual(changed.count('rrzc_io_trace::Finish('),1)
        original='      const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);\n'
        self.assertEqual(backend(original).count('ioctl('),1)

    def test_both_translation_units_share_ledger_and_thread_context(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);client=root/'client.cc';io=root/'backend.cc';binary=root/'test';trace=root/'trace.bin'
            io.write_text('#include "rrzc_io_trace.h"\nvoid device_read(unsigned long long n) {'
                'rrzc_io_trace::Read(n,false,0,n==24?1:0);}\n')
            client.write_text('#include "rrzc_io_trace.h"\nvoid device_read(unsigned long long);\n'
                'int main(){try{device_read(8);for(unsigned long long n=0;n<100;n++){'
                'rrzc_io_trace::Begin(n,false,n,n+1000);device_read(n*8);rrzc_io_trace::End(n);}'
                'rrzc_io_trace::Finish();}catch(const std::exception&){return 2;}return 0;}\n')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(HERE),
                str(client),str(io),'-o',str(binary)],check=True)
            reply=subprocess.run([str(binary)],env=dict(os.environ,RRZC_CAL_IO_TRACE=str(trace)),
                capture_output=True,text=True,check=True)
            data=list(rows(trace));self.assertEqual(len(data),101)
            self.assertEqual(data[0][0],2**64-1);self.assertEqual(data[0][1],1)
            self.assertEqual(data[-1][:2],(99,101))
            self.assertIn('前台上下文=1，请求序号=3',reply.stderr)
            self.assertIn('Key=1003，LBA=24',reply.stderr)
            failed=subprocess.run([str(binary)],env=dict(os.environ,RRZC_CAL_IO_TRACE=str(trace)),capture_output=True)
            self.assertNotEqual(failed.returncode,0)

    def test_partial_or_reordered_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'trace'
            for data in (b'wrong',b'RRZCIO01x',b'RRZCIO01'+struct.pack('<3Q',0,1,7)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):list(rows(path))

    def test_opening_hash_difference_does_not_hide_later_amount_difference(self):
        with tempfile.TemporaryDirectory() as name:
            roots=[Path(name)/tag for tag in ('a','b')]
            for n,root in enumerate(roots):
                root.mkdir();(root/'run.log').write_text('')
                (root/'请求提交.bin').write_bytes(b'RRZCREQ1'+b''.join(
                    struct.pack('<7Q',i,0,i,100+i,10+i*10,11+i*10,12+i*10) for i in range(2)))
                records=[(2**64-1,5,1),(0,6,2),(1,7,3)] if n==0 else [(2**64-1,7,9),(0,8,10),(1,10,11)]
                (root/'请求设备摘要.bin').write_bytes(b'RRZCIO01'+b''.join(struct.pack('<3Q',*r) for r in records))
            result=compare(*roots)
            self.assertEqual(result['首差']['记录序号从零起'],0)
            self.assertEqual(result['首个每请求命令量差异']['请求']['请求序号'],1)
            self.assertEqual(result['首个每请求命令量差异']['本请求期间设备读命令数'],[1,2])


if __name__=='__main__':unittest.main()
