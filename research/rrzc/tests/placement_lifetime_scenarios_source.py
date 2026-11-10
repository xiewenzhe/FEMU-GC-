"""只改变独立客户端的写Key范围；ZenFS放置、维护与分类代码保持来源分离版。"""
import difflib
import json
from pathlib import Path
import shutil

from calibration_source import once
from placement_origin_source import ROOT, digest, prepare as origin_prepare


def lifetime_header(text):
    text=once(text,'#pragma once','#pragma once\n#include "rrzc_lifetime_scenarios.h"')
    return once(text,'return (window%6)*width+hot+index;',
                'return rrzc_lifetime_scenarios::Slot(window)*width+hot+index;')


def prepare(output):
    output=Path(output)
    origin_prepare(output)
    source=output/'源码'
    header=source/'rrzc_sst_lifetime.h'
    before=header.read_text();after=lifetime_header(before);header.write_text(after)
    new=ROOT/'model/rrzc_lifetime_scenarios.h'
    shutil.copyfile(new,source/new.name)
    manifest={str(p.relative_to(source)):digest(p) for p in source.rglob('*')
              if p.is_file() and p.name!='传输身份.json'}
    (source/'传输身份.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (output/'负载代码差异.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
        fromfile='来源分离版/rrzc_sst_lifetime.h',tofile='三场景/rrzc_sst_lifetime.h'))+
        ''.join(difflib.unified_diff([],new.read_text().splitlines(True),
        fromfile='/dev/null',tofile='三场景/'+new.name)))
    proof=dict(仅写Key范围改变=True,ZenFS算法不变=True,H固定前4096键=True,
        读=786432,写=1474560,请求=2260992,种子=20261023,
        场景=['repeat','spread','switch'],SST实际寿命='须实测，不给策略未来标签',
        源码清单SHA256=digest(source/'传输身份.json'))
    (output/'负载副本身份.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof
