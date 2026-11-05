"""边界计划与反例校验；合成请求只用于校验统计器，不能当作真实实验。"""
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from run_cover_boundary_host import wrapper,valid_tag,extract,freeze_object,MODEL_PREVIOUS
from analyze_cover_boundary import CASES,check_schedule,MiB
from replay_maintenance_cover import snapshots,evaluate


def requests(life):
    data=[]
    def add(op,name='',offset=0,size=0):data.append((str(len(data)),'业务',op,name,str(offset),str(size),'0'))
    for _ in range(900):add('READ')
    add('CHECK',offset=1);add('DELETE' if life=='early' else 'KEEP','/joint/D.sst')
    for _ in range(312):add('READ')
    add('CHECK',offset=2);add('APPEND',size=256*MiB);add('CHECK',offset=3)
    add('DELETE' if life=='late' else 'KEEP','/joint/D.sst')
    for _ in range(1024):add('READ')
    add('CHECK',offset=4);return data


class BoundaryTest(unittest.TestCase):
    def test_matrix_and_wrapper_guard(self):
        self.assertEqual(len(set(CASES)),6)
        for case in CASES:
            for mode in ('greedy','model'):
                for n in (1,2):valid_tag(f'{case}-k1-{mode}-r{n}')
        for tag in ('../bad','low-early-k0-greedy-r1','low-middle-k1-model-r1','high-live-k1-model-r3'):
            with self.assertRaises(ValueError):valid_tag(tag)
        text=wrapper();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('ZENFS_RRZC_MAINTENANCE_COVER=1',text)
        self.assertIn('ZENFS_RRZC_CANDIDATE_POOL=original',text)
        self.assertIn('RRZC_CAL_GREEDY_EXECUTE="$greedy"',text)
        self.assertIn('"$target/colocation_credit" "$scenario"',text)

    def test_expiry_must_happen_on_declared_side_of_maintenance(self):
        for life in ('early','late','live'):check_schedule(requests(life),life)
        with self.assertRaises(AssertionError):check_schedule(requests('late'),'early')
        with self.assertRaises(AssertionError):check_schedule(requests('early'),'late')
        with self.assertRaises(AssertionError):check_schedule(requests('early'),'live')
        wrong=requests('late');wrong[-1],wrong[-2]=wrong[-2],wrong[-1]
        # 不能用更少的真实读取假装同工作量。
        wrong.pop(0)
        with self.assertRaises(AssertionError):check_schedule(wrong,'late')

    def test_fixed_candidate_qualification_and_exit_target(self):
        total=7808*MiB;zone=128*MiB
        spaces=[]
        for free in (1536*MiB,1024*MiB):
            percent=100*free//total;threshold=100-3*(20-percent)
            spaces.append((total*21+99)//100-free)
            for valid in (2*MiB,3*MiB//2):
                garbage=100-100*valid//zone
                self.assertGreater(garbage,threshold);self.assertLess(garbage,100)
        self.assertLess(spaces[0],126*MiB)
        self.assertGreater(spaces[1],252*MiB+MiB//2)

    def test_frozen_object_and_reader_are_reused_without_recompiling(self):
        if not MODEL_PREVIOUS.exists():self.skipTest('上一轮冻结对象未保留')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'源码').mkdir()
            binary,headers=freeze_object(root,MODEL_PREVIOUS)
            plan=json.loads((root/'源码/冻结对象清单.json').read_text())
            original=json.loads((MODEL_PREVIOUS/MODEL_PREVIOUS.name.removesuffix('-results')/'协同池构建身份.json').read_text())
            self.assertEqual(plan['对象SHA256'],original['对象SHA256'])
            self.assertIn('rrzc_maintenance_cover.h',headers)
            previous=MODEL_PREVIOUS/MODEL_PREVIOUS.name.removesuffix('-results')/'retire-k1-greedy-r1'
            frame=snapshots(previous)[0];results=[]
            for free in (1536*MiB,1024*MiB):
                row=dict(frame,需求=dict(frame['需求'],空闲=free,非空闲=7808*MiB-free))
                results.append(evaluate(binary,[row])[0]['新建议']['Zone']//(128*MiB))
            self.assertEqual(results,[5,6])

    def test_bad_archive_has_no_partial_extraction(self):
        tag='low-early-k1-greedy-r1'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'bad.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                good=tarfile.TarInfo(tag+'/log');tar.addfile(good,io.BytesIO())
                bad=tarfile.TarInfo('../escape');tar.addfile(bad,io.BytesIO())
            with self.assertRaises(ValueError):extract(archive,root,tag)
            self.assertFalse((root/tag).exists())


if __name__=='__main__':unittest.main()
