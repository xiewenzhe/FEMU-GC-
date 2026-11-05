"""8GiB快速配对的接线与空间保护测试，合成输入不进入实验结果。"""
import ast
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from run_ycsb_quick_host import (profile8,wrapper_source,probe_source,analysis_source,
    plot_source,disk_space,replace_once,QuickRun,RECORDS,OPERATIONS)
from finerr_profile import load,settings
from ycsb_af import make_properties,properties


class QuickTest(unittest.TestCase):
    def test_profile_only_scales_zone_count(self):
        old=load();new=profile8()
        self.assertEqual(old['命名空间']['容量字节'],32<<30)
        self.assertEqual(new['命名空间']['容量字节'],8<<30)
        self.assertEqual(new['命名空间']['Zone数量'],64)
        self.assertEqual(new['命名空间']['Zone字节'],128<<20)
        self.assertEqual(new['维护'],old['维护'])
        self.assertEqual(new['沿用条件'],old['沿用条件'])
        self.assertIn('devsz_mb=8192',settings(new)['设备参数'])
        self.assertIn('zns_num_blk=64',settings(new)['设备参数'])

    def test_unchanged_mechanism_and_shell_valid(self):
        text=wrapper_source();old=(ROOT/'tests/run_ycsb_af_stream_guest.sh').read_text()
        self.assertEqual(re.findall(r'(ZENFS_[A-Z0-9_]+)=([^\s\\]+)',text),
                         re.findall(r'(ZENFS_[A-Z0-9_]+)=([^\s\\]+)',old))
        self.assertIn('== 8589934592',text);self.assertIn('len(z)==64',text)
        self.assertIn('(1<<30,1<<30)',text)
        self.assertNotIn('"$target/sst_device_probe"',text)
        # 清单独立命名，不覆盖旧软件身份或包装器。
        self.assertIn('quick.software.sha256',text)
        subprocess.run(['bash','-n'],input=text,text=True,check=True)

    def test_analysis_preserves_checks_only_geometry_changes(self):
        text=analysis_source();ast.parse(text)
        original=(ROOT/'analyze_ycsb_af.py').read_text()
        restored=text.replace('8GiB','32GiB').replace("len(d['Zone']) == 64","len(d['Zone']) == 256")
        restored=restored.replace('len(initial) == 64','len(initial) == 256')
        self.assertEqual(original,restored)
        self.assertIn('历史最高RC',text)

    def test_probe_keeps_blocks_threshold(self):
        text=probe_source()
        self.assertIn('r.zones.size()!=64',text)
        self.assertIn('r.blocks_per_zone!=128',text)
        self.assertIn('r.threshold!=1024',text)

    def test_plot_honestly_labels_small_single_f(self):
        text=plot_source();ast.parse(text)
        self.assertNotIn('32 GiB',text)
        self.assertIn('单轮非稳态',text)
        self.assertIn('尚无正式配对',text)

    def test_disk_budget_does_not_lower_old_reserve(self):
        def stat(path):return SimpleNamespace(f_bavail=40<<30,f_frsize=1)
        rows=disk_space(initial=True,stat=stat)
        self.assertEqual(rows['data盘']['本阶段所需字节'],36<<30)
        rows=disk_space(stat=stat)
        self.assertEqual(rows['data盘']['本阶段所需字节'],26<<30)

    def test_data_low_rejected_even_system_ok(self):
        def stat(path):return SimpleNamespace(f_bavail=(40 if str(path)=='/' else 25)<<30,f_frsize=1)
        with self.assertRaisesRegex(ValueError,'data盘'):disk_space(stat=stat)

    def test_system_low_rejected_even_data_ok(self):
        def stat(path):return SimpleNamespace(f_bavail=(13 if str(path)=='/' else 40)<<30,f_frsize=1)
        with self.assertRaisesRegex(ValueError,'系统盘'):disk_space(stat=stat)

    def test_replacement_refuses_source_drift(self):
        with self.assertRaises(ValueError):replace_once('两处两处','两处','改动')

    def test_standard_f_counts_and_scale(self):
        base=(ROOT/'tests/workload-stable-unlimited.properties').read_text()
        source='readproportion=0.5\nreadmodifywriteproportion=0.5\nrequestdistribution=zipfian\n'
        props=properties(make_properties(base,source,'f',RECORDS,OPERATIONS))
        self.assertEqual(props['operationcount'],'300000')
        self.assertEqual(RECORDS*4096,6<<30)
        self.assertEqual(props['limit.ops'],'0')
        self.assertEqual(props['rrzc.independent_keys'],'false')

    def test_root_only_redirects_quick_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            run=QuickRun(SimpleNamespace(label='joint-quick-test'),None,Path(folder),{})
            command=f'nohup setsid bash {run.target}/run_ycsb_af_guest.sh full-f-model model f 1572864 300000 20261023'
            with patch('run_tail_ablation_fixed_pair.FixedPair.root',return_value='成功') as root:
                run.root(command)
            self.assertIn('run_ycsb_quick_guest.sh',root.call_args.args[1])
            self.assertIn('1572864 300000 20261023',root.call_args.args[1])


if __name__=='__main__':unittest.main()
