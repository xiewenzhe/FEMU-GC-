#!/usr/bin/env python3
"""宿主归档哈希核验后，回收同一临时客机内已结束实验的冗余副本。"""
import json
import os
from pathlib import Path
import shutil
import sys
import hashlib


def clean(current,previous,recovered_sha=None):
    assert os.geteuid()==0 and current.parent==previous.parent==Path('/home/femu'),'仅同一隔离客机目录'
    assert current.name.startswith('joint-controlled-') and previous.name.startswith('joint-controlled-') and current!=previous
    assert current.is_dir() and previous.is_dir() and not previous.is_symlink(),'待清理目录身份不符'
    archive=previous.with_name(previous.name+'-calibration.tar.gz')
    assert archive.is_file() and not archive.is_symlink(),'待清理客机副本的压缩归档缺失'
    recovered=previous.with_name(previous.name+'-calibration-recovered.tar.gz')
    if recovered_sha is not None:
        assert len(recovered_sha)==64 and all(c in '0123456789abcdef' for c in recovered_sha)
        assert recovered.is_file() and not recovered.is_symlink(), '恢复归档缺失'
        h=hashlib.sha256()
        with recovered.open('rb') as stream:
            for block in iter(lambda:stream.read(1<<20),b''):h.update(block)
        assert h.hexdigest()==recovered_sha, '客机恢复归档与已核验宿主SHA256不同，拒绝清理'
    # 仅查当前客机打开的文件或cwd，避免删除仍被进程使用的结果。
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():continue
        links=[proc/'cwd',proc/'exe',*(proc/'fd').glob('*')]
        for link in links:
            try:path=link.resolve(strict=True)
            except (OSError,RuntimeError):continue
            assert path!=previous and previous not in path.parents and path!=archive and path!=recovered,'旧实验仍被进程占用'
    size=sum(p.stat().st_size for p in previous.rglob('*') if p.is_file())+archive.stat().st_size
    shutil.rmtree(previous);archive.unlink()
    if recovered_sha is not None:recovered.unlink()
    (current/'已归档客机副本回收.json').write_text(json.dumps(dict(原目录=str(previous),原归档=str(archive),
        宿主已事先验证SHA256=True,删除前客机占用检查通过=True,客机释放字节估算=size),ensure_ascii=False,indent=2)+'\n')
    print('已回收客机中已归档的前轮冗余副本，字节估算='+str(size),flush=True)


if __name__=='__main__':clean(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else None)
