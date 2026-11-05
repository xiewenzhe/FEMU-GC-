"""在新客机恢复已归档逻辑输入及只读起点参考；不导入NAND内存状态。"""
import json
from pathlib import Path
import shutil
import sys
import tarfile
from restore_calibration_guest import digest


def restore(target):
    plan=json.loads((target/'固定输入复用清单.json').read_text())
    assert str(target)==plan['客机工作目录'] and target.parent==Path('/home/femu')
    payload=target/'固定输入复用.tar'
    assert digest(payload)==plan['压缩包SHA256'],'固定输入包哈希不同'
    with tarfile.open(payload) as tar:
        members=tar.getmembers()
        assert len(members)==len(plan['文件']) and {m.name for m in members}==set(plan['文件'])
        for m in members:
            p=Path(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert p.parts[0] in ('逻辑输入','参考起点')
            dest=target/p;assert not dest.exists(),'不覆盖已有输入或参考'
            dest.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src,dest.open('xb') as out:shutil.copyfileobj(src,out)
            assert digest(dest)==plan['文件'][m.name],'恢复输入成员哈希不同'
    manifest=json.loads((target/'逻辑输入/清单.json').read_text())
    assert manifest['辅助路径']==str(target/'source/aux'),'原路径未保持'
    for row in manifest['逐区']:
        assert digest(target/'逻辑输入'/(str(row['Zone'])+'.bin.gz'))==row['压缩SHA256']
    source=target/'source';source.mkdir()
    shutil.copytree(target/'逻辑输入/aux',source/'aux')
    (target/'固定输入恢复核验.json').write_text(json.dumps(dict(通过=True,来源=plan['来源'],
        所有逻辑输入逐字复用=True,未发送设备命令=True,
        边界='随后各臂独立Reset回放和真实读取准备RC；不是模拟NAND快照'),ensure_ascii=False,indent=2)+'\n')
    # 成员已全部验哈希，宿主仍保留传输包及原归档；避免再次归档冗余约2GiB包。
    size=payload.stat().st_size
    payload.unlink()
    (target/'固定输入传输包清理.json').write_text(json.dumps(dict(字节=size,
        SHA256=plan['压缩包SHA256'],原因='完整恢复及哈希核验后清理冗余传输包，原始输入和宿主来源保留'),
        ensure_ascii=False,indent=2)+'\n')
    print('原始逻辑输入逐字恢复，随后独立重建设备状态',flush=True)


if __name__=='__main__':restore(Path(sys.argv[1]))
