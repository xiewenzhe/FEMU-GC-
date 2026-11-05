#!/usr/bin/env python3
"""仅隔离客机的辅助目录诊断；不关闭fsync，不作为持久存储性能结果。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

NAME='诊断辅助盘'
MARKER='启用辅助目录tmpfs'


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def guard(target):
    assert os.geteuid()==0 and target.parent==Path('/home/femu'), '仅允许独立客机实验目录'
    assert target.name.startswith('joint-controlled-') and target.is_dir() and not target.is_symlink()
    assert (target/MARKER).is_file(), '未显式启用辅助目录诊断'
    assert 'FEMU' in Path('/sys/class/nvme/nvme0/model').read_text().upper(), '不是隔离FEMU客机'


def mounted(target):
    mount=target/NAME
    rows=[line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    found=[row for row in rows if row[4]==str(mount)]
    assert len(found)==1, '辅助盘没有唯一挂载点'
    row=found[0];tail=row[row.index('-')+1:]
    assert tail[0]=='tmpfs' and tail[1]=='rrzc-aux-diagnostic', '辅助盘挂载身份不符'
    assert not mount.is_symlink(), '挂载点不能是符号链接'
    return mount


def files(root):
    out={}
    for p in sorted(root.rglob('*')):
        assert not p.is_symlink(), '辅助文件中发现未登记符号链接'
        if p.is_file():out[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def setup(target):
    guard(target);mount=target/NAME
    assert not mount.exists(), '不覆盖原辅助盘目录'
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(mem['MemAvailable'].split()[0])*1024>=768<<20, '客机辅助盘及保护内存不足'
    mount.mkdir()
    subprocess.run(['mount','-t','tmpfs','-o','size=256m,mode=0700,nosuid,nodev',
                    'rrzc-aux-diagnostic',str(mount)],check=True)
    mounted(target)
    dump(target/'辅助盘启动.json',dict(挂载=str(mount),文件系统='tmpfs',容量上限字节=256<<20,
        边界='只隔离根ext4日志干扰，仍调用真实fsync；不是持久存储性能或收益实验'))


def restore_aux(target,result,saved,oldaux):
    mount=mounted(target)
    assert result.parent==target and result.is_dir() and result.name in ('p1','p2','g1','g2')
    assert oldaux==target/'source/aux' and saved==target/'逻辑输入/aux'
    before=files(saved);nextdir=mount/result.name
    assert not nextdir.exists(), '不覆盖之前的辅助盘分组'
    backup=result/'回放前辅助文件'
    if oldaux.is_symlink():
        assert oldaux.resolve().parent==mount and oldaux.resolve().name in ('p1','p2','g1','g2'), '原辅助链接不属于本轮诊断'
        shutil.copytree(oldaux,backup)
        oldaux.unlink()  # 仅替换已核验的本轮辅助链接；实际文件留在tmpfs等待归档。
    else:
        assert oldaux.is_dir(), '固定输入原辅助目录不存在'
        oldaux.rename(backup)
    shutil.copytree(saved,nextdir)
    assert files(nextdir)==before, '辅助输入复制哈希不一致'
    oldaux.symlink_to(nextdir,target_is_directory=True)
    dump(result/'辅助输入核验.json',dict(通过=True,文件SHA256=before,辅助目录=str(oldaux),
        实际目录=str(nextdir),文件系统='tmpfs',边界='仅诊断，非持久化辅助盘'))


def finish(target):
    guard(target);mount=target/NAME
    # 业务已经正常退出或由宿主异常路径停止；任何打开的辅助文件都拒绝卸载。
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():continue
        for link in [proc/'cwd',*(proc/'fd').glob('*')]:
            try:p=link.resolve(strict=True)
            except (OSError,RuntimeError):continue
            assert p!=mount and mount not in p.parents, '仍有进程占用辅助盘'
    mounted(target);dest=target/'辅助盘归档'
    assert not dest.exists(), '不覆盖辅助盘归档'
    before=files(mount);shutil.copytree(mount,dest)
    assert files(dest)==before, '辅助盘归档哈希不符，不卸载'
    # tmpfs根目录为0700；复制内容后仅让本轮femu账号可读归档，不扩大其他目录权限。
    owner=target.stat()
    for p in [dest,*dest.rglob('*')]:
        os.chown(p,owner.st_uid,owner.st_gid)
    assert dest.stat().st_uid==owner.st_uid and dest.stat().st_mode&0o700==0o700, '归档目录账号或可读权限不符'
    subprocess.run(['umount',str(mount)],check=True)
    assert not os.path.ismount(mount), '辅助盘未卸载'
    dump(target/'辅助盘收尾.json',dict(归档完整=True,文件SHA256=before,已卸载=True,
        说明='所有分组辅助内容复制并哈希核验后卸载，客机停机前保留磁盘归档'))


if __name__=='__main__':
    action,target=sys.argv[1],Path(sys.argv[2])
    if action=='setup':setup(target)
    elif action=='finish':finish(target)
    else:raise ValueError('未知辅助盘诊断操作')
