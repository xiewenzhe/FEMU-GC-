"""对五轮已关闭实验的FTL原始命令日志无损压缩；指标、归档和镜像不动。"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from ycsb_af import digest,require

BASE=Path('/home/data/xwz/rrzc-baselines')
NAMES=('joint-controlled-sst-sampling-v1-20261030',
       'joint-controlled-sst-interleaved-v1-20261030',
       'joint-controlled-sst-flush-w-v2-20261030',
       'joint-controlled-sst-flush-w-v3-20261030',
       'joint-controlled-flush-off-v1-20261030')


def compress(path,apply=False):
    before=path.stat();sha=digest(path);dest=path.with_suffix(path.suffix+'.gz')
    require(not path.is_symlink() and before.st_nlink==1 and not dest.exists(),
            '只处理独立普通日志，禁止覆盖压缩件')
    row=dict(原路径=str(path),压缩路径=str(dest),原始字节=before.st_size,原SHA256=sha,
             恢复='gzip -dc 压缩路径 > 原路径；再次sha256sum核对原SHA256后才供mmap分析使用')
    if not apply:return row
    partial=dest.with_suffix(dest.suffix+'.partial');require(not partial.exists(),'压缩临时件已存在')
    with path.open('rb') as source,partial.open('xb') as raw:
        with gzip.GzipFile(filename='',mode='wb',compresslevel=1,fileobj=raw,mtime=0) as out:
            shutil.copyfileobj(source,out,1<<20)
        raw.flush();os.fsync(raw.fileno())
    h=hashlib.sha256()
    with gzip.open(partial,'rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):h.update(block)
    require(h.hexdigest()==sha and digest(path)==sha and path.stat().st_ino==before.st_ino,
            '解压字节或原日志身份变化，不移除原日志')
    partial.chmod(before.st_mode&0o777);partial.rename(dest)
    row.update(压缩SHA256=digest(dest),压缩字节=dest.stat().st_size,解压SHA256=h.hexdigest())
    # 先落盘逐文件恢复凭据，再移除已经核验的未压缩副本。
    evidence=path.with_suffix(path.suffix+'.压缩核验.json')
    with evidence.open('x') as out:
        json.dump(row,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
    path.unlink();row['释放字节']=before.st_blocks*512-dest.stat().st_blocks*512
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true')
    args=p.parse_args();rows=[];archives={}
    running=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True)
    require(str(BASE)+'/' not in running.stdout,'存在本项目客机，先完成退出核验')
    for name in NAMES:
        root=BASE/(name+'-results');state=json.loads((root/'状态.json').read_text())
        stage=json.loads((root/'阶段归档.json').read_text())
        require(stage['完整'] and not stage['客机仍存活'] and state['当前客机PID'] is None,
                '归档或关闭未通过：'+name)
        archive=Path(state['镜像处理']['archive']);expected=state['镜像处理']['archive_sha256']
        require(digest(archive)==expected,'完整归档哈希不符：'+name);archives[str(archive)]=expected
        rows.append(compress(root/'FEMU设备命令.bin',args.apply))
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
    result=dict(已执行=args.apply,范围=list(NAMES),日志=rows,完整归档SHA256=archives,
                边界='只压缩FTL诊断原始日志；解压字节逐字核验；原归档、指标、普通日志及镜像不改')
    if args.apply:
        with (BASE/'配对准入空间整理-20261102.json').open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
