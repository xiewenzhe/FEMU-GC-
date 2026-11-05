"""校准入口的请求不变、起始拒绝和计时闭合边界；合成数据不作实验结果。"""
import csv
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from calibration_source import patched,wrapper,without_hooks
from calibration_guest import gate
from calibration_guest import warmup
from unittest.mock import patch
import copy
from analyze_calibration import timing,background_overlap
from dbbench_hotspot_source import patched as hot
from dbbench_rotating_source import patched as rotating

ROOT=Path(__file__).resolve().parents[1]


class CalibrationTest(unittest.TestCase):
    def test_guest_patch_import_has_no_host_environment_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('calibration_source.py',):
                shutil.copyfile(ROOT/'tests'/name,Path(folder)/name)
            subprocess.run([sys.executable,'-I','-c',
                'import sys;sys.path.insert(0,sys.argv[1]);from calibration_source import patched',folder],check=True)

    def test_only_new_benchmarks_are_added(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-parallel-v2-20261023-results/源码/db_bench_tool.cc')
        if not source.exists():self.skipTest('原生归档源不存在')
        before=rotating(hot(source.read_text()));after=patched(before)
        marker='  void RRZCRotating(ThreadState* thread) {'
        self.assertEqual(before.split(marker,1)[1],without_hooks(after.split(marker,1)[1]))
        self.assertIn('chunk!=2048',after)
        self.assertIn('fresh_db = true;',after)
        self.assertEqual(after.count('if (method != &Benchmark::RRZCCalibrationMark) total_thread_count_++;'),1)
        self.assertEqual(before.count('new ThreadState(i, total_thread_count_)'),after.count('new ThreadState(i, total_thread_count_)'))
        self.assertIn('Number("RRZC_CAL_TLS_SEED",0)',after)
        self.assertEqual(after.count('RRZCSeedTls(thread);'),2)
        self.assertIn('Number("RRZC_CAL_SINGLE_BACKGROUND",0)',after)
        self.assertLess(after.index('auto paused=db->db->PauseBackgroundWork();'),
                        after.index('env->SetBackgroundThreads(0,Env::Priority::HIGH);'))
        self.assertLess(after.index('env->SetBackgroundThreads(1,Env::Priority::LOW);'),
                        after.index('auto resumed=db->db->ContinueBackgroundWork();'))

    def test_background_events_detect_overlap_and_missing_completion(self):
        def log(rows):
            return '\n'.join('EVENT_LOG_v1 '+json.dumps(dict(event=e,job=j)) for e,j in rows)
        serial=[('flush_started',1),('flush_finished',1),('compaction_started',2),('compaction_finished',2)]
        self.assertEqual(background_overlap(log(serial))['重叠次数'],0)
        concurrent=[serial[0],serial[2],serial[1],serial[3]]
        self.assertEqual(background_overlap(log(concurrent))['重叠次数'],1)
        with self.assertRaisesRegex(ValueError,'未闭合'):background_overlap(log(serial[:-1]))
        with self.assertRaisesRegex(ValueError,'缺少匹配'):background_overlap(log(serial[1:]))

    def test_wrapper_keeps_original_business_and_thresholds(self):
        source=(ROOT/'tests/run_all_blocks_dbbench_guest.sh').read_text()
        out=wrapper(source)
        subprocess.run(['bash','-n'],input=out,text=True,check=True)
        for marker in ['--reads=786432 --writes=589824','--seed=20261023','--benchmark_write_rate_limit=0',
                       'ZENFS_RRZC_PAIR_GREEDY="$greedy"','ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1']:
            self.assertIn(marker,out)
        self.assertEqual(out.count('rrzctick'),6)
        self.assertLess(out.index('calibration_guest.py" gate'),out.index('hotspot=1;phase run'))

    def fixture(self,path):
        path.mkdir()
        (path/'装载-0.sst.csv').write_text('文件,层级,字节,最小键十六进制,最大键十六进制\n/1.sst,1,4096,00,01\n')
        (path/'装载-0.extent.csv').write_text('文件,Zone,起点,字节\n/1.sst,402653184,402653184,4096\n')
        rows=[dict(Zone=z,块=i,Channel=0,LUN=0,Plane=0,物理块=i,RC=0,代际=1,
            十秒增量=0,三十秒增量=0,标志=7) for z in range(64) for i in range(128)]
        (path/'blocks.before.json').write_text(json.dumps(rows))
        (path/'space.before.json').write_text(json.dumps({'Zone':[dict(编号=3,WP=402657280,剩余=134213632,有效=4096,容量=134217728)]}))
        (path/'zones.before.json').write_text(json.dumps({'zone_list':[
            dict(slba=z*262144,wp=z*262144,cap=262144,state='EMPTY',attrs=0,attrs_info=0) for z in range(64)]}))

    def test_gate_preserves_generations_but_rejects_changed_rc(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'g1',Path(folder)/'g2';self.fixture(a);self.fixture(b)
            self.assertTrue(gate(a,a)['通过']);self.assertTrue(gate(b,a)['通过'])
            p=b/'blocks.before.json';rows=json.loads(p.read_text());rows[3*128]['代际']=9
            p.write_text(json.dumps(rows));self.assertTrue(gate(b,a)['通过'])
            rows[3*128]['RC']=1;p.write_text(json.dumps(rows))
            with self.assertRaisesRegex(AssertionError,'起始状态不一致'):gate(b,a)
            self.assertTrue(gate(b,a,'low')['通过'])
            self.assertFalse(gate(b,a,'low')['RC严格相同'])
            rows[3*128]['RC']=11;p.write_text(json.dumps(rows))
            with self.assertRaisesRegex(AssertionError,'起始RC超过'):gate(b,a,'low')

    def test_timing_closes_and_rejects_missing_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);lines=[]
            for i in range(24):
                for role,count in [('READ',32768),('WRITE',24576)]:
                    lines.append(f'RRZC批次完成：操作={role}，批次={i}，累计={(i+1)*count}，热点起点={(i//4)*65536}，键校验=1，开始纳秒={30+i*10}，完成纳秒={38+i*10}\n')
            lines += [f'校准阶段边界：序号={i}，纳秒={n}\n' for i,n in enumerate([10,20,300,400,500,600])]
            (root/'run.log').write_text(''.join(lines));(root/'run.start.ns').write_text('0');(root/'run.end.ns').write_text('700')
            out=timing(root);self.assertAlmostEqual(out['整阶段秒'],700/1e9)
            (root/'run.log').write_text(''.join(lines[:-1]))
            with self.assertRaisesRegex(ValueError,'阶段标记'):timing(root)

    def test_warmup_uses_only_counted_reads_and_waits_out_recent_window(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'装载-0.extent.csv').write_text('文件,Zone,起点,字节\n/1.sst,402653184,402653184,2097152\n')
            rows=[dict(代际=1,设备会话=[1,2],RC=8 if i==12 else 0) for i in range(128)]
            def read(fd,page):rows[page%128]['RC']+=1
            def stats():return dict(阈值触发事件=0,自身刷新豁免读取=0,计入RC读取=sum(r['RC'] for r in rows))
            with patch('calibration_guest.os.open',return_value=9),patch('calibration_guest.os.close'),\
                 patch('calibration_guest.blocks',side_effect=lambda _:copy.deepcopy(rows)),\
                 patch('calibration_guest.raw_read',side_effect=read),patch('calibration_guest.read_stats',side_effect=stats),\
                 patch('calibration_guest.time.sleep') as quiet:
                warmup(root)
            quiet.assert_called_once_with(31)
            self.assertTrue(all(r['RC']==32 for r in rows))
            proof=json.loads((root/'真实准备读取.json').read_text())
            self.assertEqual(proof['逐区'][0]['真实NAND读增量'],32*128-8)

    def test_matched_gate_requires_exact_sst_rc_and_zero_recent_deltas(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'g1',Path(folder)/'g2'
            for p in (a,b):
                self.fixture(p)
                rows=json.loads((p/'blocks.before.json').read_text())
                for r in rows:
                    r['十秒增量']=r['三十秒增量']=0
                    if r['Zone']==3:r['RC']=32
                (p/'blocks.before.json').write_text(json.dumps(rows))
                (p/'真实准备读取.json').write_text(json.dumps({'目标RC':32,'逐区':[{'Zone':3,'可读物理块':128}]}))
            self.assertTrue(gate(b,a,'matched')['RC严格相同'])
            original=json.loads((b/'blocks.before.json').read_text())
            changed=copy.deepcopy(original);changed[0]['RC']=1
            (b/'blocks.before.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(AssertionError,'完整可观测起点'):gate(b,a,'matched')
            (b/'blocks.before.json').write_text(json.dumps(original))
            z=json.loads((b/'zones.before.json').read_text());z['zone_list'][0]['state']='CLOSED'
            (b/'zones.before.json').write_text(json.dumps(z))
            with self.assertRaisesRegex(AssertionError,'完整可观测起点'):gate(b,a,'matched')
            z['zone_list'][0]['state']='EMPTY';(b/'zones.before.json').write_text(json.dumps(z))
            rows[3*128]['RC']=31;(b/'blocks.before.json').write_text(json.dumps(rows))
            with self.assertRaisesRegex(AssertionError,'准备读数'):gate(b,a,'matched')


if __name__=='__main__':unittest.main()
