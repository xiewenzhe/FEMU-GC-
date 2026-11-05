"""重复性诊断口径和目录隔离测试；合成数字不能作为业务结果。"""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
import sys
import json
import hashlib
from unittest.mock import patch

from analyze_repeat_order import comparisons, ratio, ARMS
from run_repeat_order_host import tagged_wrapper
from run_triggered_gc_host import wait_stage, stop_business


class RepeatOrderTest(unittest.TestCase):
    def row(self, copy=100, throughput=100, read=100, write=100):
        return {'总迁移MiB':copy,'阶段吞吐ops':throughput,'READ平均微秒':read,'WRITE平均微秒':write}

    def test_same_greedy_is_unit_ratio(self):
        rows = {tag:self.row() for tag,_ in ARMS}
        result = comparisons(rows)
        self.assertTrue(all(v==1 for r in result for k,v in r.items() if k!='比较'))

    def test_reverse_pair_always_uses_greedy_denominator(self):
        rows = {tag:self.row() for tag,_ in ARMS}
        rows['g2'] = self.row(copy=110,throughput=80)
        rows['reverse-model'] = self.row(copy=80,throughput=120)
        control,reverse = comparisons(rows)[:2]
        self.assertAlmostEqual(control['总迁移MiB比值'],1.1)
        self.assertAlmostEqual(control['阶段吞吐ops比值'],.8)
        self.assertAlmostEqual(reverse['总迁移MiB比值'],.8)
        self.assertAlmostEqual(reverse['阶段吞吐ops比值'],1.2)

    def test_zero_denominator_is_not_fabricated(self):
        with self.assertRaisesRegex(ValueError,'分母必须为正'):
            ratio(0,10)

    def test_tagged_wrapper_keeps_actual_strategy_and_load(self):
        base = (Path(__file__).parent/'run_all_blocks_dbbench_guest.sh').read_text()
        text = tagged_wrapper(base)
        self.assertEqual(text.replace('''arm_tag=${RRZC_ARM_TAG:-$mode}
[[ $arm_tag =~ ^[a-z0-9-]+$ ]]
result=$target/$arm_tag''','result=$target/$mode',1),base)
        with tempfile.TemporaryDirectory(prefix='rrzc-repeat-wrapper-') as folder:
            script = Path(folder)/'run.sh'
            script.write_text(text)
            subprocess.run(['bash','-n',str(script)],check=True)
        with self.assertRaisesRegex(ValueError,'不唯一'):
            tagged_wrapper('')

    def test_arm_tag_cannot_inject_command(self):
        with patch('run_triggered_gc_host.disk_space'):
            with self.assertRaisesRegex(ValueError,'实际策略非法'):
                wait_stage(SimpleNamespace(), 'g1;bad', 'run.sh', 1, mode='greedy')

    def test_failed_phase_logs_are_linked_before_shutdown(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-failure-capture-') as folder:
            root=Path(folder);experiment=root/'experiment';stage=experiment/'g1';stage.mkdir(parents=True)
            logs=root/'logs';logs.mkdir();source=root/'source';source.mkdir()
            (source/'zbd_zenfs.cc').write_text('合成源码，仅用于采证测试')
            log=logs/'zenfs_nvme0n1_failed.log';log.write_text('原始失败日志')
            (stage/'phase.txt').write_text('verify')
            (stage/'verify.logs.before.txt').write_text('')
            scripts=[s for s in stop_business.__code__.co_consts if isinstance(s,str) and 'captured=[]' in s]
            self.assertEqual(len(scripts),1)
            script=scripts[0].replace("Path('/tmp')",'Path('+repr(str(logs))+')')
            script=script.replace("Path('/home/femu/rocksdb/plugin/zenfs/fs')",'Path('+repr(str(source))+')')
            subprocess.run([sys.executable,'-c',script,str(experiment)],check=True)
            saved=stage/'failed.verify.zenfs_nvme0n1_failed.log'
            self.assertTrue(saved.samefile(log))
            self.assertEqual((experiment/'失败源码/zbd_zenfs.cc').read_text(),'合成源码，仅用于采证测试')

    def test_reuse_build_preserves_executable_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-reuse-build-') as folder:
            root=Path(folder);source=root/'before';target=root/'after';source.mkdir();target.mkdir()
            for p in (source,target):(p/'expected.json').write_text('{}')
            binary=source/'probe';binary.write_text('#!/bin/sh\necho 合成测试程序\n');binary.chmod(0o700)
            manifest={'客机目录':str(source),'文件SHA256':{'probe':hashlib.sha256(binary.read_bytes()).hexdigest()}}
            (target/'复用构建.json').write_text(json.dumps(manifest,ensure_ascii=False))
            helper=Path(__file__).parent/'reuse_build_guest.py'
            subprocess.run([sys.executable,str(helper),str(target)],check=True,stdout=subprocess.DEVNULL)
            self.assertIn('合成测试程序',subprocess.check_output([str(target/'probe')],text=True))
            binary.write_text('已篡改')
            failed=subprocess.run([sys.executable,str(helper),str(target)],capture_output=True,text=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertIn('来源构建文件改变',failed.stderr)


if __name__ == '__main__':
    unittest.main()
