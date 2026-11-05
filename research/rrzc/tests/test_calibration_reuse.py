"""受控客户端复用的身份准入；合成夹具不代表实验结果。"""
import json
import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_calibration_host as m
from run_triggered_gc_host import check_reused_image_budget


class CalibrationReuseTest(unittest.TestCase):
    def test_project_budget_refuses_unbounded_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'result.bin').write_bytes(b'x'*4096)
            with patch.object(m,'IMAGE_DIR',root):
                used=m.check_baseline_budget(0,limit=1<<20)
                self.assertGreater(used,0)
                with self.assertRaisesRegex(Exception,'空间上限'):
                    m.check_baseline_budget(1,limit=used)

    def test_reused_image_budget_stops_growth(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'work.qcow2'
            image.write_bytes(b'x'*4096)
            allocated=image.stat().st_blocks*512
            run=SimpleNamespace(args_reuse_image=image,max_image_bytes=allocated)
            check_reused_image_budget(run)
            run.max_image_bytes=allocated-1
            with self.assertRaisesRegex(Exception,'空间上限'):
                check_reused_image_budget(run)

    def test_calibration_archive_keeps_large_input_only_in_tar(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source';(source/'example/逻辑输入').mkdir(parents=True)
            (source/'example/g1').mkdir()
            (source/'example/逻辑输入/1.bin.gz').write_bytes(b'compressed input')
            (source/'example/逻辑输入/清单.json').write_text('{}')
            (source/'example/g1/run.log').write_text('evidence')
            archive=out/'example-calibration.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                tar.add(source/'example',arcname='example')
            sha=hashlib.sha256(archive.read_bytes()).hexdigest()
            run=object.__new__(m.CalibrationRun)
            run.output=out;run.target='/home/femu/example';run.tag='calibration'
            run.deployed=True;run.socket=None;run.state={'当前客机PID':None}
            run.ssh=lambda command,*args: sha+'  archive' if command.startswith('sha256sum') else ''
            run.scp=lambda:['scp']
            run.command=lambda argv,*args: subprocess.run(argv,check=True) if argv[0]=='tar' else ''
            run.close_guest=lambda:None
            run.record=lambda **values:run.state.update(values)
            run.finish_image=lambda *args,**kwargs:None
            run.finish()
            self.assertFalse((out/'example/逻辑输入/1.bin.gz').exists())
            self.assertEqual((out/'example/逻辑输入/清单.json').read_text(),'{}')
            self.assertEqual((out/'example/g1/run.log').read_text(),'evidence')
            self.assertTrue(archive.is_file())

    def fixture(self,folder):
        base=Path(folder);label='joint-controlled-source';out=base/(label+'-results')
        root=out/label;root.mkdir(parents=True)
        args=SimpleNamespace(reuse_client=label,load_keys=917504,initial_rc_policy='matched',
            match_rc=True,fix_tls=True,front_order=True,logical_input=True,single_background=True,
            drain_background=True,request_trace=True,device_event_trace=False,flush_wait_trace=True,
            gc_gate=True,write_buffers=2,target_trace=True,aux_tmpfs=True,diagnostic_rounds=24,
            synchronous_space=True,matched_observation=False,file_opening_threads=16)
        plan=dict(初始装载键数=917504,RC策略='matched',真实块计数准备=True,内部随机性控制=True,
            前台固定全局顺序=True,固定逻辑输入=True,共享单后台线程=True,每次写后排空后台=True,
            逐请求跟踪=True,设备稀疏事件=False,Flush同步分段=True,GC批次检查门=True,最大写缓冲数=2,
            辅助目录tmpfs诊断=True,目标分配稀疏诊断=True,实际业务批次数=24,受控空间请求确认=True)
        names=('db_bench','原始-db_bench_tool.cc','校准-db_bench_tool.cc','原始-flush_job.cc',
            '诊断-flush_job.cc','Flush诊断身份.json','校准链接映射.txt','原生Make计划.txt',
            'Flush原生构建计划.txt','诊断-sync_point.cc','诊断-sync_point_impl.cc','同步点头文件.h',
            'fs_all_blocks.o','原始-zbd_zenfs.cc','目标采证-zbd_zenfs.cc','边界受控-fs_zenfs.cc',
            'rrzc_boundary_sync.h')
        for name in names:(root/name).write_text('合成身份：'+name)
        (root/'校准客户端身份.json').write_text(json.dumps(dict(客户端SHA256=m.digest(root/'db_bench'),
            ZenFS对象SHA256=m.digest(root/'fs_all_blocks.o'))))
        (root/'目标采证身份.json').write_text(json.dumps(dict(组合对象SHA256=m.digest(root/'fs_all_blocks.o'),
            受控批次检查=True,受控空间请求确认=True)))
        prior=base/'prior';(prior/'源码').mkdir(parents=True)
        (prior/'源码/expected.json').write_text('{}');(root/'expected.json').write_text('{}')
        tar=out/(label+'-calibration.tar.gz');tar.write_bytes(b'archive-fixture')
        (out/'状态.json').write_text(json.dumps(dict(标签=label,当前客机PID=None,
            镜像处理=dict(archive_sha256=m.digest(tar)))))
        (out/'阶段归档.json').write_text(json.dumps(dict(完整=True,客机仍存活=False,业务成功=True)))
        (out/'预注册计划.json').write_text(json.dumps(plan))
        return base,prior,args,out,root,plan

    def test_reuses_same_object_and_captures_its_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            base,prior,args,_,root,_=self.fixture(folder)
            with patch.object(m,'IMAGE_DIR',base),patch.object(m,'PREVIOUS',prior):
                result=m.reused_client_manifest(args)
            self.assertEqual(result['文件SHA256']['fs_all_blocks.o'],m.digest(root/'fs_all_blocks.o'))
            self.assertIn('边界受控-fs_zenfs.cc',result['文件SHA256'])
            self.assertIn('rrzc_boundary_sync.h',result['文件SHA256'])

    def test_rejects_different_schedule_or_altered_object(self):
        for change in ('调度','对象'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                base,prior,args,out,root,plan=self.fixture(folder)
                if change=='调度':
                    plan['受控空间请求确认']=False
                    (out/'预注册计划.json').write_text(json.dumps(plan))
                else:(root/'fs_all_blocks.o').write_text('被改动的合成对象')
                with patch.object(m,'IMAGE_DIR',base),patch.object(m,'PREVIOUS',prior):
                    with self.assertRaises(Exception):m.reused_client_manifest(args)


if __name__=='__main__':unittest.main()
