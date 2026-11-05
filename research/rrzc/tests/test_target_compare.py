"""双目标真实适配函数与独立接线检查；不把合成后端称为设备实验。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import pair_admission_source
import target_compare_source as patch

ROOT=Path(__file__).resolve().parents[1]
FROZEN=Path('/home/data/xwz/rrzc-baselines/joint-controlled-sst-flush-w-v3-20261030-results')
FS=FROZEN/FROZEN.name.removesuffix('-results')/'协同池副本/plugin/zenfs/fs'


class TargetTest(unittest.TestCase):
    def test_analysis_rejects_invalid_alternative_and_wrong_execution(self):
        from analyze_target_compare import decisions
        fields=dict(序号=1,执行=1,实际普通=1,建议普通=1,可比=1,普通存在=1,普通风险安全=1,
                    协同有效=30,协同账目=30,协同H=10,普通有效=15,普通账目=15,普通H=0,
                    协同剩余=100,普通剩余=90,原因='合成合法支配，不是实测')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'log'
            def write(changes):
                row=dict(fields,**changes)
                path.write_text('双目标比较决定：'+'，'.join(k+'='+str(v) for k,v in row.items())+'\n')
            write({});_,summary=decisions(path,1);self.assertEqual(summary['直接选择普通次数'],1)
            for changes in (dict(普通有效=40,普通账目=40),dict(普通剩余=101),
                            dict(普通风险安全=0),dict(执行=0),dict(序号=2)):
                write(changes)
                with self.assertRaises(ValueError):decisions(path,1)

    def test_real_adapter_and_policy_compile_and_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'test'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_target_compare.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_source_keeps_allocation_commits_after_comparison(self):
        old=pair_admission_source.adapter((FS/'rrzc_colocation_zenfs.inc').read_text())
        new=patch.adapter(old)
        with self.assertRaises(ValueError):patch.adapter(new)
        self.assertLess(new.index('CompareMigrationTargets(z'),new.index('bool active=false'))
        self.assertIn('target_observe_ && !own_open',new)
        self.assertIn('alternative->CheckRelease()',new)
        old_header=pair_admission_source.zbd_header((FS/'zbd_zenfs.h').read_text())
        new_header=patch.header(old_header)
        with self.assertRaises(ValueError):patch.header(new_header)
        self.assertIn('target_compare_=false,target_observe_=false',new_header)
        # 本转换器只接共享目标适配和成员，没有触碰GC/RR选源及文件元数据。
        self.assertEqual(new.split('IOStatus ZonedBlockDevice::RestoreColocation',1)[1].split(
            'IOStatus ZonedBlockDevice::TryColocation',1)[0],old.split(
            'IOStatus ZonedBlockDevice::RestoreColocation',1)[1].split(
            'IOStatus ZonedBlockDevice::TryColocation',1)[0])

    def test_wrappers_only_enable_business_and_keep_greedy_argument(self):
        text=pair_admission_source.business_wrapper((FROZEN/'源码/run_pool_business_guest.sh').read_text())
        text=patch.business_wrapper(text)
        subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('target_on="${RRZC_TARGET_ARM:-0}";pair_on=0;',text)
        self.assertIn('target_on=0;pair_on=0;flush_w=0;hotspot=0;phase verify',text)
        self.assertIn('ZENFS_RRZC_POOL_TARGET_OBSERVE=1',text)
        for value in (0,1):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);(p/'arm.sh').write_text(patch.arm_wrapper(value))
                (p/'run_pool_business_guest.sh').write_text(
                    '#!/bin/bash\nset -eu\n[[ $# == 1 && $1 == greedy ]]\nprintf "%s" "$RRZC_TARGET_ARM"\n')
                self.assertEqual(subprocess.check_output(['bash',str(p/'arm.sh'),'greedy'],text=True),str(value))


if __name__=='__main__':unittest.main()
