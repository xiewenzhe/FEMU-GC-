"""期望RR的YCSB接线及配对隔离测试；合成样本不是设备实验。"""
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
import run_expected_ycsb_host as host
import analyze_expected_ycsb as analysis
import capture_expected_ycsb_guest as capture
from run_rr_fair_long_v2_host import CollectedRun


class ExpectedYcsbTest(unittest.TestCase):
    def test_new_wiring_and_original_wrapper_isolation(self):
        for name in ('build_expected_ycsb_guest.sh','run_expected_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(host.STAGES,('smoke','short'))
        self.assertEqual(host.ExpectedYcsbRun.finish,CollectedRun.finish)
        self.assertEqual(host.ExpectedYcsbRun.observe,CollectedRun.observe)
        self.assertEqual(host.ExpectedYcsbRun.close_guest,CollectedRun.close_guest)
        text=(ROOT/'tests/run_expected_ycsb_guest.sh').read_text()
        for item in ('ZENFS_RRZC_EXPECTED_RR="$window"','ZENFS_RRZC_PERSISTENT_CREDIT="$window"',
            'ZENFS_RRZC_RECLAIM_BYTES=0','ZENFS_RR_FAIR_QUEUE=1','YCSB_PHASE_TIMING=1'):
            self.assertIn(item,text)
        self.assertNotIn('ZENFS_RRZC_EXPECTED_RR',(ROOT/'tests/run_window_ycsb_guest.sh').read_text())
        self.assertNotIn('GC_START',text);self.assertNotIn('reset-zone',text)

    def test_adapter_deployed_alias_and_strict_paths(self):
        for name in ('smoke-greedy','smoke-expected','short-greedy','short-expected'):
            self.assertTrue(capture.valid_path(Path('/home/femu/rrzc-sst-results')/name))
        for name in ('short-window','long-expected','../short-expected'):
            self.assertFalse(capture.valid_path(Path('/home/femu/rrzc-sst-results')/name))
        with tempfile.TemporaryDirectory() as tmp:
            for source,target in (('capture_ycsb_guest.py','capture_ycsb_core.py'),
                ('capture_expected_ycsb_guest.py','capture_ycsb_guest.py')):
                shutil.copyfile(ROOT/'tests'/source,Path(tmp)/target)
            subprocess.run([sys.executable,str(Path(tmp)/'capture_ycsb_guest.py'),'--help'],check=True,capture_output=True)

    def test_link_guard_requires_new_switches_and_utf8(self):
        script=(ROOT/'tests/build_expected_ycsb_guest.sh').read_text()
        guard=script.split('python3 - "$target" <<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
        names=('ZENFS_RRZC_WINDOW_CREDIT','ZENFS_RRZC_PAIR_GREEDY','窗口收益模式：',
            'ZENFS_RRZC_PERSISTENT_CREDIT','ZENFS_RRZC_EXPECTED_RR','期望RR配置：','期望RR成本：')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'ycsb';folder.mkdir()
            for omitted in (None,*names):
                (folder/'ycsb').write_bytes('\0'.join(s for s in names if s!=omitted).encode())
                result=subprocess.run([sys.executable,'-',tmp],input=guard,text=True,capture_output=True)
                self.assertEqual(result.returncode==0,omitted is None)

    def test_business_launch_failure_still_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=host.ExpectedYcsbRun(SimpleNamespace(label='joint-expected-test'),None,Path(tmp),{})
            (Path(tmp)/'同库身份.json').write_text('{}')
            frozen=Path(tmp)/'源码';frozen.mkdir();run.freeze_extra(frozen)
            with patch.object(run,'start',side_effect=RuntimeError('合成启动失败')),patch.object(run,'finish') as finish:
                with self.assertRaisesRegex(RuntimeError,'合成启动失败'):run.arm('smoke','expected')
                self.assertEqual(finish.call_count,1)

    def test_source_is_real_completed_and_reaudited(self):
        if not host.MANIFEST.is_file():self.skipTest('缺真实原结果，不伪造通过')
        expected,pairs=host.checked_source()
        self.assertIn('plugin/zenfs/fs/rrzc_expected_rr_selector.h',expected)
        self.assertEqual(set(pairs),set(host.CASES))
        self.assertTrue(all(r['配对验收通过'] for r in pairs.values()))

    def test_pair_rejects_trace_library_workload_and_live_guest(self):
        sample=dict(请求SHA256='合成请求',最终值SHA256='合成值',RR次数=2,ZC次数=3,
            总迁移字节=100,总成功ZoneReset=6,推导物理Block擦除次数=768)
        with tempfile.TemporaryDirectory() as tmp:
            for mode in host.MODES:
                out=Path(tmp)/('short-'+mode);out.mkdir();folder=out/out.name;folder.mkdir()
                (out/'阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=False)))
                for name in ('software.before.log','workload.properties'):(folder/name).write_text('合成相同')
            b=copy.deepcopy(sample);b['RR次数']=1
            with patch.object(analysis,'analyze_arm',side_effect=[sample,b]):
                self.assertEqual(analysis.compare_pair(tmp,'short')['模型相对Greedy差值']['RR次数'],-1)
            for key in ('请求SHA256','最终值SHA256'):
                changed=dict(b);changed[key]='合成不同'
                with patch.object(analysis,'analyze_arm',side_effect=[sample,changed]),self.assertRaises(ValueError):
                    analysis.compare_pair(tmp,'short')
            folder=Path(tmp)/'short-expected/short-expected'
            for name in ('software.before.log','workload.properties'):
                (folder/name).write_text('合成不同')
                with patch.object(analysis,'analyze_arm',side_effect=[sample,b]),self.assertRaises(ValueError):
                    analysis.compare_pair(tmp,'short')
                (folder/name).write_text('合成相同')
            (folder.parent/'阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=True)))
            with patch.object(analysis,'analyze_arm',side_effect=[sample,b]),self.assertRaises(ValueError):
                analysis.compare_pair(tmp,'short')


if __name__=='__main__':unittest.main()
