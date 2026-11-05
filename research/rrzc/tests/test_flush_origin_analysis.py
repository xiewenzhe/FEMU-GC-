"""合成日志只检验来源证据拒绝，不作为设备实验结果。"""
import json
from pathlib import Path
import tempfile
import unittest
from analyze_flush_origin import verify_origins


class AnalysisTest(unittest.TestCase):
    def test_requires_real_flush_job_and_same_restore_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            events=[dict(event='flush_started',job=3),dict(event='compaction_started',job=4),
                    dict(event='table_file_creation',job=3,file_number=462,status='OK')]
            (p/'run.rocksdb.log').write_text('\n'.join('EVENT_LOG_v1 '+json.dumps(e) for e in events))
            line='Flush来源W标记：文件编号=15，文件名=/rrzc-dbbench/000462.sst，任务=3，标签=2，未来寿命未知=1\n'
            (p/'run.log').write_text(line)
            restore='Flush来源挂载文件：文件编号=15，文件名=/rrzc-dbbench/000462.sst，任务=3，标签=2，成员数=0\n'
            (p/'verify.zenfs.log').write_text(restore);(p/'verify.log').write_text('内容校验成功\n')
            (p/'布局-61.extent.csv').write_text('文件\n/rrzc-dbbench/000462.sst\n')
            proof,_=verify_origins(p,{})
            self.assertEqual(proof['重挂载有效来源数'],1)
            (p/'run.log').write_text(line.replace('任务=3','任务=4'))
            with self.assertRaises(ValueError):verify_origins(p,{})
            (p/'run.log').write_text(line);(p/'verify.zenfs.log').write_text(restore.replace('任务=3','任务=9'))
            with self.assertRaises(ValueError):verify_origins(p,{})


if __name__=='__main__':unittest.main()
