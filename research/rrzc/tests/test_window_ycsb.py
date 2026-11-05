"""窗口YCSB接线、配对拒绝和监督复用测试；合成样本不冒充真实运行。"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import analyze_window_ycsb as analysis
import run_window_ycsb_host as host
import capture_window_ycsb_guest as capture
from run_rr_fair_long_v2_host import CollectedRun, business_key


class WindowYcsbTest(unittest.TestCase):
    def test_syntax_files_frozen_model_and_short_scope(self):
        for n in ('build_window_ycsb_guest.sh','run_window_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(host.STAGES,('smoke','short'))
        self.assertEqual(host.PARENT.name,'joint-window-credit-v2-20261021-build.qcow2')
        self.assertEqual(analysis.sizes('short'),(491520,400000,4096,65536,30))
        wrapper=(ROOT/'tests/run_window_ycsb_guest.sh').read_text()
        for token in ('ZENFS_RRZC_WINDOW_CREDIT="$window"','ZENFS_RRZC_PAIR_GREEDY="$greedy"',
            'ZENFS_RRZC_STABLE_CREDIT=0','ZENFS_RR_FAIR_QUEUE=1','ZENFS_RRZC_RECLAIM_BYTES=0'):
            self.assertIn(token,wrapper)
        self.assertNotIn('GC_START',wrapper);self.assertNotIn('reset-zone',wrapper)
        props=(ROOT/'tests/workload-stable-unlimited.properties').read_text()
        self.assertIn('limit.ops=0',props);self.assertNotIn('limit.file=',props)

    def test_corrected_supervisor_and_failure_capture_are_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=host.WindowYcsbRun(SimpleNamespace(label='joint-window-unit'),None,Path(tmp),{})
            self.assertEqual(run.observe.__func__,CollectedRun.observe)
            self.assertEqual(run.finish.__func__,CollectedRun.finish)
            self.assertEqual(run.close_guest.__func__,CollectedRun.close_guest)
            self.assertEqual(run.target,'/home/femu/joint-window-unit')
            self.assertNotEqual(run.control,run.target)
            self.assertFalse(run.deployed)
        self.assertEqual(business_key(dict(装载完成=True,最近状态=['10','50'])),
                         business_key(dict(装载完成=True,最近状态=['190','50'])))

    def test_capture_alias_import_and_path_restrictions(self):
        for tag in ('smoke-greedy','smoke-window','short-greedy','short-window'):
            self.assertTrue(capture.valid_path(Path('/home/femu/rrzc-sst-results')/tag))
        for path in ('/tmp/short-window','/home/femu/rrzc-sst-results/long-window',
                     '/home/femu/rrzc-sst-results/short-stable'):
            self.assertFalse(capture.valid_path(Path(path)))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copyfile(ROOT/'tests/capture_ycsb_guest.py',root/'capture_ycsb_core.py')
            shutil.copyfile(ROOT/'tests/capture_window_ycsb_guest.py',root/'capture_ycsb_guest.py')
            subprocess.run([sys.executable,str(root/'capture_ycsb_guest.py'),'--help'],
                           check=True,capture_output=True)

    def test_stage_capture_directory_does_not_collide_with_inherited_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp);frozen=output/'源码';frozen.mkdir()
            for n in ('capture_ycsb_guest.py','capture_window_ycsb_guest.py'):
                shutil.copyfile(ROOT/'tests'/n,frozen/n)
            run=host.WindowYcsbRun(SimpleNamespace(label='joint-window-unit'),None,output,{})
            directories=[]
            def ssh(command,*_):
                if command.startswith('test ! -e '):
                    path=command.split()[3]
                    self.assertNotIn(path,directories)
                    directories.append(path)
                if command.startswith('sha256sum '):
                    n='capture_ycsb_guest.py' if command.endswith('capture_ycsb_core.py') else 'capture_window_ycsb_guest.py'
                    return host.digest(frozen/n)+'  '+command.split()[1]+'\n'
                return ''
            with patch.object(host.WindowRun,'start'),patch.object(run,'ssh',side_effect=ssh),\
                 patch.object(run,'command'):
                for mode in ('build','smoke-greedy','smoke-window','short-greedy','short-window'):
                    run.start(mode)
                    self.assertTrue(run.deployed)
                    self.assertTrue(run.control.endswith('-evidence-'+mode))
            self.assertEqual(len(set(directories)),5)

    def test_relink_guard_rejects_missing_utf8_or_switch(self):
        source=(ROOT/'tests/build_window_ycsb_guest.sh').read_text()
        guard=source.split('python3 - "$target" <<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'ycsb').mkdir();binary=root/'ycsb/ycsb'
            # 仅为合成链接标记样本，不是可执行程序或实验结果。
            full=b'ZENFS_RRZC_WINDOW_CREDIT\0ZENFS_RRZC_PAIR_GREEDY\0'+'窗口收益模式：'.encode()
            binary.write_bytes(full)
            subprocess.run([sys.executable,'-',tmp],input=guard,text=True,check=True,capture_output=True)
            binary.write_bytes(b'ZENFS_RRZC_PAIR_GREEDY\0')
            self.assertNotEqual(subprocess.run([sys.executable,'-',tmp],input=guard,text=True,capture_output=True).returncode,0)

    def pair(self,tmp):
        sample=dict(请求SHA256='合成相同请求',最终值SHA256='合成相同最终值',RR次数=2,ZC次数=3,
                    总迁移字节=100,总成功ZoneReset=6,推导物理Block擦除次数=768)
        for mode in ('greedy','window'):
            out=Path(tmp)/('short-'+mode);out.mkdir();folder=out/('short-'+mode);folder.mkdir()
            (out/'阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=False)))
            (folder/'software.before.log').write_text('合成同库: OK\n')
            (folder/'workload.properties').write_text('limit.ops=0\n')
        return sample

    def test_pair_rejects_trace_library_workload_or_live_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.pair(tmp);b=copy.deepcopy(a);b['RR次数']=1
            with patch.object(analysis,'analyze_arm',side_effect=[a,b]):
                self.assertEqual(analysis.compare_pair(tmp,'short')['模型相对Greedy差值']['RR次数'],-1)
            for key in ('请求SHA256','最终值SHA256'):
                bad=copy.deepcopy(b);bad[key]='合成不同内容'
                with patch.object(analysis,'analyze_arm',side_effect=[a,bad]),self.assertRaises(ValueError):
                    analysis.compare_pair(tmp,'short')
            folder=Path(tmp)/'short-window/short-window'
            for name in ('software.before.log','workload.properties'):
                previous=(folder/name).read_text();(folder/name).write_text('合成不同条件')
                with patch.object(analysis,'analyze_arm',side_effect=[a,b]),self.assertRaises(ValueError):
                    analysis.compare_pair(tmp,'short')
                (folder/name).write_text(previous)
            (Path(tmp)/'short-window/阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=True)))
            with patch.object(analysis,'analyze_arm',side_effect=[a,b]),self.assertRaises(ValueError):
                analysis.compare_pair(tmp,'short')

    def test_original_controlled_source_is_reaudited(self):
        if not host.MANIFEST.is_file():self.skipTest('真实源短对照不可用，不伪造通过')
        expected,cases=host.checked_source()
        self.assertIn('plugin/zenfs/fs/rrzc_window_credit_selector.h',expected)
        self.assertEqual(len(cases),4)
        self.assertTrue(all(r['配对验收通过'] for r in cases.values()))


if __name__=='__main__':unittest.main()
