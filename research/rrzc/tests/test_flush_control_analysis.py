"""关闭标签不能把真实Flush邻居错误统计为不存在；合成布局只作解析反例。"""
import json
from pathlib import Path
import tempfile
import unittest
from analyze_flush_control import composition


class CompositionTest(unittest.TestCase):
    def test_unlabelled_flush_neighbour_still_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            rows=[dict(event='flush_started',job=2),dict(event='compaction_started',job=3),
                  dict(event='table_file_creation',job=2,file_number=462,status='OK'),
                  dict(event='table_file_creation',job=3,file_number=463,status='OK')]
            (p/'run.rocksdb.log').write_text('\n'.join('EVENT_LOG_v1 '+json.dumps(r) for r in rows))
            for n in range(62):
                (p/f'布局-{n}.extent.csv').write_text('文件,Zone,字节\n/rrzc-dbbench/000039.sst,939524096,17\n'
                    '/rrzc-dbbench/000462.sst,939524096,48\n/rrzc-dbbench/000463.sst,939524096,9\n')
            result,count=composition(p,{'/rrzc-dbbench/000039.sst':'H'},'off','p1')
            self.assertEqual(count,1);self.assertEqual(len(result),62)
            self.assertEqual(result[0]['本轮Flush输出字节'],48)
            self.assertEqual(result[0]['其他字节'],9)
            self.assertEqual(result[0]['ZoneID'],7)


if __name__=='__main__':unittest.main()
