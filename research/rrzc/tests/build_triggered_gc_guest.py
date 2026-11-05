#!/usr/bin/env python3
"""重编已修正ZenFS翻译单元并优先链接边界程序和db_bench，原静态库不变。"""
import json
from pathlib import Path
import sys

import build_all_blocks_guest as model
import build_all_blocks_dbbench_guest as client


def main():
    target = Path(sys.argv[1])
    expected = json.loads((target / 'expected.json').read_text())
    for name, value in expected.items():
        assert model.digest(model.ROOT / name) == value, '编译前来源不符：' + name
    # 复用已验证的原生Make编译参数及对象优先链接检查，不改写库和旧对象。
    model.main()
    client.OBJECT = target / 'fs_all_blocks.o'
    client.main()
    for executable in ('all_blocks_device', 'db_bench', 'space_probe'):
        assert '触发后选区约束：'.encode() in (target / executable).read_bytes(), '程序没有链接修正对象'
    print('修正ZenFS完整翻译单元、边界客户端、db_bench及空间探针编译链接通过', flush=True)


if __name__ == '__main__':
    main()
