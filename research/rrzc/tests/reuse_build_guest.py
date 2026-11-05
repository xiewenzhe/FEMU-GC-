#!/usr/bin/env python3
"""校验来源源码与二进制身份后复用已成功构建的修复程序，不重复生成构建版本。"""
from pathlib import Path
import hashlib
import json
import shutil
import sys

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
target=Path(sys.argv[1]);manifest=json.loads((target/'复用构建.json').read_text())
source=Path(manifest['客机目录'])
expected=json.loads((target/'expected.json').read_text())
assert expected==json.loads((source/'expected.json').read_text()),'已编译来源不是同一修复源码'
for name,sha in expected.items():assert digest(Path('/home/femu/rocksdb')/name)==sha,'当前源码或库身份不同：'+name
for name,sha in manifest['文件SHA256'].items():
    assert digest(source/name)==sha,'来源构建文件改变：'+name
    shutil.copyfile(source/name,target/name)
    shutil.copymode(source/name,target/name)
(target/'复用构建核验.json').write_text(json.dumps(dict(通过=True,来源=manifest),ensure_ascii=False,indent=2))
print('共同修复源码、原库及全部复用程序SHA256通过，不重复编译',flush=True)
