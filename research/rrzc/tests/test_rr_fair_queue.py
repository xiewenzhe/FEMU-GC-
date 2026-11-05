"""RR反饥饿修复的真实C++核与补丁边界；不放松原30秒健康检查。"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rrzc_rr_health import inspect,fields,GRACE


class FairQueueTest(unittest.TestCase):
    def test_cpp_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_rr_fair_queue.cc'),'-o',str(binary)],check=True,capture_output=True)
            self.assertIn('实例隔离边界通过',subprocess.check_output([str(binary)],text=True))

    def test_actual_failure_is_still_rejected_not_relabelled(self):
        p=Path('/home/data/xwz/rrzc-baselines/joint-stable-ycsb-v1-20261021-results/short-stable/short-stable/zenfs.run.log')
        if not p.is_file():self.skipTest('真实失败日志不可用，不伪造证据')
        result=inspect(p.read_text())
        self.assertEqual(GRACE,30*10**9);self.assertFalse(result['验收通过'])
        self.assertTrue(any(r['Zone']==3221225472 and r['代际']=='0' and r['等待秒']>40
                            for r in result['超期明细']))
        # 初次同时待维护后，旧实现多次重新维护低地址3区，24仍在原代际。
        seen=[]
        for line in p.read_text().splitlines():
            if 'RR风险状态：' in line:
                r=fields(line,'RR风险状态：')
                if r['Zone']=='3221225472' and r['代际']=='0' and int(r['最大RC'])>=1024:seen.append(r)
        self.assertEqual(len(seen),5)

    def test_patch_on_archived_source_and_header_roundtrip(self):
        parent=Path('/home/data/xwz/rrzc-baselines/joint-stable-credit-v1-20261021-results/build/joint-stable-credit-v1-20261021/fs_zenfs.cc')
        header=Path('/home/data/xwz/rrzc-baselines/joint-rr-fair-ycsb-v1-20261021-results/rr-build/joint-rr-fair-ycsb-v1-20261021-rr-fix/before/fs_zenfs.h')
        if not header.is_file():header=ROOT.parents[1]/'build-femu/rrzc/v1-growth-work/fs/fs_zenfs.h'
        if not parent.is_file() or not header.is_file():self.skipTest('真实源码归档不可用')
        with tempfile.TemporaryDirectory() as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            shutil.copyfile(parent,fs/'fs_zenfs.cc');shutil.copyfile(header,fs/'fs_zenfs.h')
            patch=(ROOT/'zenfs-rr-fair-queue.patch').read_text()
            before={p.name:p.read_bytes() for p in fs.iterdir()}
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            after=(fs/'fs_zenfs.cc').read_text()
            self.assertIn('ZENFS_RR_FAIR_QUEUE',after)
            self.assertIn('std::stable_sort(pressure.zones.begin()',after)
            # 原RR迁移主体、返回和GCWorker完全不改；只增加队列更新和服务日志。
            anchor='    Info(logger_, "RR 开始：'
            self.assertEqual(after.split(anchor)[1],parent.read_text().split(anchor)[1])
            self.assertEqual(after.split('IOStatus ZenFS::ReadReclaimOneZone()')[0],parent.read_text().split('IOStatus ZenFS::ReadReclaimOneZone()')[0])
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            self.assertEqual({p.name:p.read_bytes() for p in fs.iterdir()},before)

    def test_scripts_and_disabled_default(self):
        subprocess.run(['bash','-n',str(ROOT/'tests/build_rr_fair_guest.sh'),str(ROOT/'tests/run_stable_ycsb_guest.sh')],check=True)
        wrapper=(ROOT/'tests/run_stable_ycsb_guest.sh').read_text()
        self.assertIn('${ZENFS_RR_FAIR_QUEUE:-0}',wrapper)
        self.assertIn('"${rr_extra[@]}"',wrapper)
        self.assertNotIn('limit.ops=1000',wrapper)

    def test_repair_launcher_enables_both_arms_only(self):
        import run_rr_fair_ycsb_host as host
        run=host.FairRun.__new__(host.FairRun);run.target='/home/femu/单元测试'
        for mode in ('greedy','stable'):
            command=f'nohup setsid bash {run.target}/run_stable_ycsb_guest.sh short-{mode} {mode} 400000 < /dev/null &'
            with patch.object(host.ycsb.StableYcsbRun,'root',return_value='已拦截') as mocked:
                self.assertEqual(run.root(command),'已拦截')
                self.assertIn('setsid env ZENFS_RR_FAIR_QUEUE=1 bash',mocked.call_args.args[0])
        with patch.object(host.ycsb.StableYcsbRun,'root',return_value='已拦截') as mocked:
            run.root('systemctl poweroff')
            self.assertEqual(mocked.call_args.args[0],'systemctl poweroff')


if __name__=='__main__':unittest.main()
