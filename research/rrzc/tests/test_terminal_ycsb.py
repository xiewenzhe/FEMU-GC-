"""第二版YCSB接线和验收隔离测试；合成证据只测试拒绝，不冒充实验。"""
import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import run_terminal_ycsb_host as host
import analyze_terminal_ycsb as analysis
from analyze_window_ycsb import analyze_arm as shared
from run_rr_fair_long_v2_host import CollectedRun

class TerminalYcsbTest(unittest.TestCase):
    def test_scope_and_cleanup(self):
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(len(host.FILES),len({p.name for p in host.FILES}))
        self.assertEqual(host.TerminalYcsbRun.allowed_build_changes,())
        self.assertEqual(host.TerminalYcsbRun.finish,CollectedRun.finish)
        self.assertEqual(host.TerminalYcsbRun.observe,CollectedRun.observe)
        self.assertEqual(host.STAGES,('smoke','short'))
        self.assertEqual(host.PARENT.name,'joint-terminal-v2-20261023-build.qcow2')
        script=(ROOT/'tests/run_terminal_ycsb_guest.sh').read_text()
        for value in ('pool=expanded','planning=1','ZENFS_RRZC_JOINT_TIME=1',
                      'ZENFS_RRZC_TERMINAL="$terminal"','ZENFS_RRZC_RECLAIM_BYTES=0'):
            self.assertIn(value,script)
        self.assertNotIn('GC_START',script);self.assertNotIn('reset-zone',script)
        self.assertIn('[[ $mode != model ]] || terminal=1',script)
        for name in ('build_terminal_ycsb_guest.sh','run_terminal_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)

    def test_link_guard(self):
        script=(ROOT/'tests/build_terminal_ycsb_guest.sh').read_text()
        guard=script.split('python3 - "$target" <<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
        markers=('ZENFS_RRZC_JOINT_TIME','ZENFS_RRZC_TERMINAL','边界配置：版本=2，',
                 '时序方案决策：','边界方案决策：','ZENFS_RR_STRICT')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'ycsb';folder.mkdir()
            for omitted in (None,*markers):
                (folder/'ycsb').write_bytes('\0'.join(m for m in markers if m!=omitted).encode())
                result=subprocess.run([sys.executable,'-',tmp],input=guard,text=True,capture_output=True)
                self.assertEqual(result.returncode==0,omitted is None)

    def test_new_entry_requires_explicit_versions(self):
        with self.assertRaisesRegex(ValueError,'同范围三组须显式'):
            shared('/不存在','smoke','model',same_scope=True)
        with self.assertRaisesRegex(ValueError,'边界YCSB须显式'):
            shared('/不存在','smoke','greedy',space_plan=True,space_plan_version=3,
                same_scope=True,joint_time=True,terminal_version=2)

    def test_failure_still_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=host.TerminalYcsbRun(SimpleNamespace(label='joint-terminal-ycsb-unit'),None,Path(tmp),{})
            (Path(tmp)/'同库身份.json').write_text('{}')
            with patch.object(run,'start',side_effect=RuntimeError('合成启动失败')),patch.object(run,'finish') as finish:
                with self.assertRaisesRegex(RuntimeError,'合成启动失败'):run.arm('smoke','model')
                self.assertEqual(finish.call_count,1)

    def test_pair_rejects_mismatch(self):
        sample=dict(请求SHA256='合成请求',最终值SHA256='合成值',RR次数=2,ZC次数=3,总迁移字节=100,
            总成功ZoneReset=6,推导物理Block擦除次数=768,业务末态硬件可写剩余字节=1000)
        with tempfile.TemporaryDirectory() as tmp:
            for mode in host.MODES:
                parent=Path(tmp)/('short-'+mode);folder=parent/parent.name;folder.mkdir(parents=True)
                (parent/'阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=False)))
                for name in ('software.before.log','workload.properties'):(folder/name).write_text('合成相同')
            values=[copy.deepcopy(sample) for _ in host.MODES]
            with patch.object(analysis,'analyze_arm',side_effect=values):
                self.assertTrue(analysis.compare_three(tmp,'short')['三组验收通过'])
            for key in ('请求SHA256','最终值SHA256'):
                changed=copy.deepcopy(values);changed[-1][key]='合成不同'
                with patch.object(analysis,'analyze_arm',side_effect=changed),self.assertRaises(ValueError):
                    analysis.compare_three(tmp,'short')
            (Path(tmp)/'short-model/workload.properties').write_text('不参与对照')
            (Path(tmp)/'short-model/short-model/workload.properties').write_text('合成不同')
            with patch.object(analysis,'analyze_arm',side_effect=values),self.assertRaises(ValueError):
                analysis.compare_three(tmp,'short')

if __name__=='__main__':unittest.main()
