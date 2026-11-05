"""阶段计时补丁、整数账目及真实归档回归；合成数据不声称实验收益。"""
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from client_phase import phases
from analyze_window_ycsb import compare_pair


class ClientPhaseTest(unittest.TestCase):
    def test_cpp_accounting_and_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_client_phase.cc'),'-o',str(binary)],check=True,capture_output=True)
            text=subprocess.check_output([str(binary)],text=True)
        result=phases(text,4)
        self.assertTrue(result['可知']);self.assertEqual(result['前台请求秒'],40/1e9)
        self.assertEqual(result['Cleanup秒'],20/1e9)
        for bad in (text+text,text.replace('完整=1','完整=0'),text.replace('线程数=1','线程数=2'),
                    text.replace('Cleanup纳秒=20','Cleanup纳秒=21'),text.replace('前台开始=100','前台开始=-100')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):phases(bad,4)
        with self.assertRaises(ValueError):phases(text,5)
        self.assertFalse(phases('无阶段字段的旧日志',4)['可知'])

    def test_patch_roundtrip_on_actual_client_and_no_load_change(self):
        archive=Path('/home/data/xwz/rrzc-baselines/joint-window-ycsb-v2-20261021-results/build/joint-window-ycsb-v2-20261021/client-source.tar.gz')
        if not archive.is_file():self.skipTest('实际客户端源码不可用，不伪造补丁成功')
        patch=(ROOT/'tests/ycsb-phase-timing.patch').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'core').mkdir()
            with tarfile.open(archive) as stream:
                original={n:stream.extractfile(n).read() for n in ('core/client.h','core/ycsbc.cc')}
            for n,data in original.items():(root/n).write_bytes(data)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            changed=(root/'core/ycsbc.cc').read_text()
            before=original['core/ycsbc.cc'].decode()
            key='const int total_ops = stoi(props[ycsbc::CoreWorkload::OPERATION_COUNT_PROPERTY]);'
            self.assertEqual(before.split(key)[0],changed.split(key)[0])
            self.assertGreater(changed.index('rrzc_client_phase::Global().Arm'),changed.index(key))
            self.assertGreater(changed.index('rrzc_client_phase::Global().Print'),changed.index('status_future.wait();',changed.index(key)))
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            for n,data in original.items():self.assertEqual((root/n).read_bytes(),data)

    def test_build_and_wrapper_enable_only_new_timed_client(self):
        for n in ('build_window_client_guest.sh','build_window_ycsb_guest.sh','run_window_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)
        for n in ('build_window_client_guest.sh','run_window_ycsb_guest.sh'):
            self.assertIn('YCSB_PHASE_TIMING=1',(ROOT/'tests'/n).read_text())
        self.assertNotIn('YCSB_PHASE_TIMING',(ROOT/'tests/run_stable_ycsb_guest.sh').read_text())
        self.assertNotIn('ycsb-phase-timing',(ROOT/'tests/build_sst_probe_guest.sh').read_text())

    def test_completed_real_pair_passes_without_rewriting_old_results(self):
        root=Path('/home/data/xwz/rrzc-baselines/joint-window-ycsb-v2-20261021-results')
        if not (root/'short-window/short-window/zenfs.run.log').is_file():self.skipTest('实际短对照不可用')
        state=(root/'状态.json').read_bytes();result=compare_pair(root,'short')
        self.assertTrue(result['配对验收通过'])
        self.assertEqual(result['模型相对Greedy差值']['总迁移字节'],-96793724)
        self.assertFalse(result['模式']['window']['客户端阶段耗时']['可知'])
        self.assertEqual((root/'状态.json').read_bytes(),state)


if __name__=='__main__':unittest.main()
