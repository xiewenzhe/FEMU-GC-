"""三场景共同客户端完整构建及原G1设备功能回归；无完整业务。"""
import json
from pathlib import Path
import shutil

import run_placement_origin_build_host as entry
from placement_lifetime_scenarios_source import prepare
from placement_origin_source import digest

LABEL='joint-ls-b1-20261110'


def configure():
    entry.LABEL=LABEL
    entry.prepare=prepare
    previous=entry.prepare_payload
    def payload(output):
        result=previous(output)
        frozen=output/'场景构建入口';frozen.mkdir()
        for path in (Path(__file__),Path(__file__).with_name('placement_lifetime_scenarios_source.py'),
                     Path(__file__).with_name('test_lifetime_scenarios.py'),
                     Path(__file__).parents[1]/'model/rrzc_lifetime_scenarios.h'):
            shutil.copyfile(path,frozen/path.name)
        (frozen/'SHA256.json').write_text(json.dumps({p.name:digest(p) for p in frozen.iterdir()},ensure_ascii=False,indent=2)+'\n')
        return result
    entry.prepare_payload=payload


if __name__=='__main__':
    configure();entry.main()
