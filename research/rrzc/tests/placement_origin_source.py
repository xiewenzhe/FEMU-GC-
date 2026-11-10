"""来源/寿命分离的独立副本；不增加准入阈值或另一套放置算法。"""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

from calibration_source import once

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/home/data/xwz/rrzc-baselines/joint-controlled-placement-exit-fix-v2-20261106-results')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def io_header(text):
    text = once(text, '#pragma once', '#pragma once\n#include "rrzc_file_characteristics.h"')
    anchor = '  uint8_t ColocationTag() {\n'
    facts = '''  // 当前F事实与H观察分开表达；不存在“Flush所以短命”的推断。
  rrzc_file_characteristics::Observed PlacementCharacteristics() {
    const bool hot=rrzc_stream::Enabled() && stream_profile_.Snapshot(file_size_).hot;
    return {flush_origin_w_,hot};
  }
'''
    text = once(text, anchor, facts + anchor)
    return once(text,
        'if(rrzc_stream::Enabled())return (flush_origin_w_?2:0) | (stream_profile_.Snapshot(file_size_).hot?1:0);',
        'if(rrzc_stream::Enabled())return PlacementCharacteristics().LegacySourceMask();')


def pool_header(text):
    return once(text,
        'enum class Tag : uint8_t { kNormal=0, kReadHot=1, kShortLived=2, kBoth=3 };',
        '// kShortLived仅保留旧源码拼写兼容；在线位2一直是Flush来源，并非寿命预测。\n'
        'enum class Tag : uint8_t { kNormal=0, kReadHot=1, kFlushOrigin=2,\n'
        '                           kShortLived=kFlushOrigin, kBoth=3 };')


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('不覆盖已有副本')
    original = BASE/'源码'
    identity = json.loads((original/'传输身份.json').read_text())
    if not all(digest(original/p) == h for p, h in identity.items()):
        raise ValueError('冻结来源身份变化')
    output.mkdir(parents=True)
    source = output/'源码'
    shutil.copytree(original, source)
    old_fs = original/'协同池副本/plugin/zenfs/fs'
    fs = source/'协同池副本/plugin/zenfs/fs'
    changes = {}
    for name, transform in [('io_zenfs.h', io_header), ('rrzc_colocation_pool.h', pool_header)]:
        before = (fs/name).read_text()
        after = transform(before)
        (fs/name).write_text(after)
        changes[name] = (before, after)
    header = ROOT/'model/rrzc_file_characteristics.h'
    shutil.copyfile(header, fs/header.name)
    # 编译命令可能优先搜索客户端所在目录；同名依赖必须来自同一副本。
    for name in ('rrzc_file_characteristics.h', 'rrzc_colocation_pool.h'):
        shutil.copyfile(fs/name, source/name)
    patch = ''.join(''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
        fromfile='冻结G2/'+name, tofile='来源分离/'+name)) for name, (a, b) in changes.items())
    patch += ''.join(difflib.unified_diff([], header.read_text().splitlines(True),
        fromfile='/dev/null', tofile='来源分离/'+header.name))
    (output/'代码差异.patch').write_text(patch)
    protected = ('fs_zenfs.cc', 'io_zenfs.cc', 'zbd_zenfs.cc', 'zbd_zenfs.h',
                 'rrzc_colocation_zenfs.inc', 'rrzc_stream_profile.h', 'rrzc_placement_mode.h',
                 'rrzc_placement_trace.h')
    unchanged = {name: digest(old_fs/name) == digest(fs/name) for name in protected}
    if not all(unchanged.values()):
        raise ValueError('非授权运行源码变化')
    manifest = {str(p.relative_to(source)): digest(p) for p in source.rglob('*')
                if p.is_file() and p.name != '传输身份.json'}
    (source/'传输身份.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    second = json.loads((source/'二阶副本身份.json').read_text())
    second['新源码'] = {p.name: digest(p) for p in fs.iterdir() if p.is_file()}
    second['来源寿命分离'] = dict(默认寿命='未知',预测器已实现=False,
        放置算法改变=False,历史W位值=2,历史位值含义='Flush-origin，不是短寿命',
        行为对照='旧G0/G1/G2均保持；不创建与G1重复的新模式')
    (source/'二阶副本身份.json').write_text(json.dumps(second, ensure_ascii=False, indent=2)+'\n')
    # 身份文件自身更新后重新冻结，避免传输验证仍引用旧内容。
    manifest['二阶副本身份.json'] = digest(source/'二阶副本身份.json')
    (source/'传输身份.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    proof = dict(来源=str(BASE),旧G2程序SHA256='db1c4489f9bd29eecf83d07f04cce0b94c30dc2af61b72f6b87f44759973088d',
        来源清单SHA256=digest(original/'传输身份.json'),保护正文未改变=unchanged,
        独立副本=True,算法改变=False,持久化格式改变=False,对象成员改变=False,
        新预测器=False,完整ZenFS编译=False,客机或业务实验=False,
        修改文件=list(changes)+[header.name],源码清单SHA256=digest(source/'传输身份.json'))
    (output/'副本身份.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
    return proof
