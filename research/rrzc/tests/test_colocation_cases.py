"""检查请求闭合、证据解包和检查门不改变选区；合成记录不作为实验收益。"""
from pathlib import Path
import io
import subprocess
import tarfile
import tempfile
import unittest
from analyze_colocation_cases import logical_requests
from run_colocation_cases_host import wrapper,extract
from controlled_gc_source import patched
from colocation_source import fs_source


class CasesTest(unittest.TestCase):
    def test_wrapper_has_real_limits_and_same_program(self):
        text=wrapper();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('"$target/colocation_cases" "$scenario" "$result" "$k"',text)
        self.assertIn('ZENFS_RRZC_SIMPLE_MODE=valid',text)
        self.assertIn('--enable_gc',text)
        self.assertIn("r['mar']==11 and r['mor']==7",text)
        for tag in ('smoke-k0-r1','w-first-k1-r2','rr-first-k0-r2','cooled-k1-r1'):
            answer=subprocess.check_output(['bash','-c','tag="$1"; scenario=${tag%-k*}; k=${tag#*-k}; k=${k%%-*}; echo "$scenario $k"','bash',tag],text=True)
            self.assertEqual(answer.strip(),tag.rsplit('-k',1)[0]+' '+tag.rsplit('-k',1)[1][0])

    def test_gate_leaves_entire_original_selection_body(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-repeat-order-v6-20261026-results/源码/修复后-fs_zenfs.cc')
        if not source.is_file():self.skipTest('冻结源缺失')
        a=fs_source(source.read_text());b=patched(a)
        anchor='    uint64_t non_free = zbd_->GetUsedSpace()'
        self.assertEqual(a.split(anchor,1)[1],b.split(anchor,1)[1])

    def test_requests_must_close_and_match_without_time(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);p=root/'请求.csv'
            header='序号,阶段,状态,操作,文件,偏移,字节,参数,纳秒\n'
            p.write_text(header+'0,业务,提交,READ,/H.sst,0,4096,0,10\n')
            with self.assertRaises(AssertionError):logical_requests(root)
            p.write_text(p.read_text()+'0,业务,完成,READ,/H.sst,0,4096,0,11\n')
            self.assertEqual(logical_requests(root)[0][2],'READ')
            p.write_text(p.read_text().replace('完成,READ','完成,WRITE'))
            with self.assertRaises(AssertionError):logical_requests(root)

    def test_archive_rejects_links_and_nested_paths(self):
        for path,kind in (('w-first-k0-r1/deep/file',tarfile.REGTYPE),
                          ('w-first-k0-r1/aux',tarfile.SYMTYPE),('../escape',tarfile.REGTYPE)):
            with self.subTest(path=path),tempfile.TemporaryDirectory() as name:
                base=Path(name);archive=base/'test.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    member=tarfile.TarInfo(path);member.type=kind;tar.addfile(member,io.BytesIO())
                with self.assertRaises(ValueError):extract(archive,base,'w-first-k0-r1')
                self.assertFalse((base/'w-first-k0-r1').exists())


if __name__=='__main__':unittest.main()
