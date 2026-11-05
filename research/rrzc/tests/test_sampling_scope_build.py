"""真实冻结组件与隔离部署依赖核验；不把构建自检称为设备收益。"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from sampling_scope_bundle import freeze,closed,VERIFIED_DB,IMAGE_DIR
from ycsb_af import digest

ROOT=Path(__file__).resolve().parent


class BuildTest(unittest.TestCase):
    def test_real_components_can_rebuild_original_object_and_match_frontend(self):
        prior=IMAGE_DIR/'joint-controlled-sst-cohort0-v1-20261030-results'
        guest=closed(prior);identity=json.loads((guest/'生命周期构建核验.json').read_text())
        source=closed(VERIFIED_DB);build=json.loads((source/'真实DB构建核验.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp);(target/'源码').mkdir();freeze(target,prior,identity,build)
            plan=json.loads((target/'源码/采样组件清单.json').read_text())
            self.assertEqual(len(plan['文件']),9)
            self.assertEqual(plan['原ZenFS对象SHA256'],identity['冻结ZenFS对象SHA256'])
            binary=target/'old.o'
            subprocess.run(['ld','-r','-o',str(binary),*map(str,sorted((source/'复用ZenFS对象').glob('*.o')))],check=True)
            self.assertEqual(digest(binary),plan['原ZenFS对象SHA256'])

    def test_builder_imports_with_only_deployed_dependencies(self):
        names=('build_sampling_scope_guest.py','build_all_blocks_guest.py','build_all_blocks_dbbench_guest.py',
               'all_blocks_scope_source.py','sst_lifetime_source.py','calibration_source.py')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for name in names:shutil.copyfile(ROOT/name,folder/name)
            subprocess.run(['python3','-I','-c','import sys;sys.path.insert(0,sys.argv[1]);import build_sampling_scope_guest',str(folder)],check=True)


if __name__=='__main__':unittest.main()
