"""只重编来源分离副本并验证既有G1设备路径，不运行完整业务。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
import tarfile
from types import SimpleNamespace

import run_placement_integration_host as common
from placement_origin_source import digest, prepare

LABEL = 'joint-origin-b1-20261110'


def prepare_payload(output):
    copy = output/'独立构建副本'
    prepare(copy)
    source = copy/'源码'
    (output/'源码').symlink_to(source, target_is_directory=True)
    frozen = output/'本轮入口源码'
    frozen.mkdir()
    for name in (Path(__file__), Path(__file__).with_name('placement_origin_source.py'),
                 Path(__file__).with_name('test_placement_origin.py'),
                 Path(__file__).parents[1]/'model/rrzc_file_characteristics.h'):
        shutil.copyfile(name, frozen/name.name)
    common.stamp(frozen/'SHA256.json', {p.name:digest(p) for p in frozen.iterdir()})
    payload = output/'传输.tar.gz'
    with tarfile.open(payload, 'x:gz') as archive:
        for path in source.rglob('*'):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(source)), recursive=False)
    return payload


def resources():
    result = common.preflight()
    result.update(目录已占字节=common.check_baseline_budget(8<<30),新增构建预算字节=8<<30)
    common.require(result['data可用字节']>=42<<30 and result['可用内存字节']>=30<<30,
                   '原完整构建保护线不足')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    budget = resources()
    if args.check:
        print(json.dumps(budget, ensure_ascii=False))
        return
    output = common.IMAGE_DIR/(LABEL+'-results')
    common.require(not output.exists(), '不覆盖已存在的来源分离构建')
    password = getpass.getpass('来源分离独立构建认证（仅内存）：')
    output.mkdir()
    try:
        payload = prepare_payload(output)
        common.stamp(output/'预注册计划.json', dict(模式='G1',完整业务=False,
            新算法=False,新寿命预测=False,来源寿命分离=True,资源=budget,
            独立五单元编译=True,GC启用=False,RR阈值=1024,原G2保留=True,
            边界='既有G1的来源集中及H迁移拒绝功能；自然变H的F不主动移走，不声称完全隔离'))
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        if args.detach and not common.detach_supervisor(output):
            return
        common.execute(SimpleNamespace(label=LABEL,mode='G1',reuse_program=None),
                       password,output,payload)
    finally:
        password = None


if __name__ == '__main__':
    main()
