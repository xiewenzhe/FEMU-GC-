"""元数据准备仅增加一次真实读取，不放宽逐块比较；替身结果不作实验数据。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from metadata_start_guest import align


class MetadataStartTest(unittest.TestCase):
    def test_one_read_repairs_only_observed_block_and_equal_state_needs_no_read(self):
        for rc,count in ((4,1),(5,0)):
            with self.subTest(rc=rc),tempfile.TemporaryDirectory() as name:
                root=Path(name)/'g1';root.mkdir()
                rows=[dict(Zone=z,块=0,Channel=0,LUN=0,Plane=0,物理块=z,RC=rc if z==1 else 0) for z in range(3)]
                expected=copy.deepcopy(rows);expected[1]['RC']=5
                (root.parent/'元数据起点目标.json').write_text(json.dumps(expected))
                reads=[]
                def read(fd,page):
                    self.assertEqual(page,32768);reads.append(page);rows[1]['RC']+=1
                def stats():return {'实际NAND读取':len(reads),'阈值触发事件':0}
                with patch('metadata_start_guest.os.open',return_value=100),patch('metadata_start_guest.os.close'):
                    align(root,lambda z:[copy.deepcopy(rows[z])],read,stats)
                self.assertEqual(len(reads),count)
                self.assertEqual(json.loads((root/'元数据起点补齐.json').read_text())['真实普通读取次数'],count)

    def test_never_lowers_rc_or_reads_unrecognized_difference(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)/'g1';root.mkdir()
            row=dict(Zone=1,块=0,Channel=0,LUN=0,Plane=0,物理块=1,RC=6)
            target=dict(row,RC=5)
            (root.parent/'元数据起点目标.json').write_text(json.dumps([target]))
            with patch('metadata_start_guest.os.open') as opened,self.assertRaises(AssertionError):
                align(root,lambda z:[copy.deepcopy(row)] if z==1 else [],lambda *_:None,lambda:{})
            opened.assert_not_called()

    def test_explicit_first_arm_records_p1_and_rejects_p2_as_reference(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);(base/'启用本轮元数据参考').write_text('新放置')
            (base/'元数据参考首臂.txt').write_text('p1\n')
            for tag in ('p1','p2'):(base/tag).mkdir()
            query=lambda z:[dict(Zone=z,块=0,Channel=0,LUN=0,Plane=0,物理块=z,RC=7)]
            stats=lambda:dict(实际NAND读取=0,阈值触发事件=0)
            with self.assertRaises(AssertionError):align(base/'p2',query,lambda *_:None,stats)
            with patch('metadata_start_guest.os.open',return_value=100),patch('metadata_start_guest.os.close'):
                align(base/'p1',query,lambda *_:self.fail('首臂不能伪造读取'),stats)
            proof=json.loads((base/'p1/元数据参考建立.json').read_text())
            self.assertEqual(proof['来源'],'本轮p1真实逐块查询')
            self.assertEqual(proof['元数据读取次数'],0)

    def test_second_arm_cannot_be_declared_as_first(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);(base/'p2').mkdir();(base/'启用本轮元数据参考').write_text('新放置')
            (base/'元数据参考首臂.txt').write_text('p2\n')
            with self.assertRaises(AssertionError):align(base/'p2',lambda _:[],lambda *_:None,lambda:{})


if __name__=='__main__':unittest.main()
