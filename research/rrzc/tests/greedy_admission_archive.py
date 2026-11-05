"""准入只收集直接证据文件；不跟随aux链接或读取客机数据库目录。"""
from pathlib import Path
import re
import tarfile
from ycsb_af import digest,require


def archive_command(target,tag='g1'):
    require(re.fullmatch(r'/home/femu/joint-controlled-[a-z0-9-]+',target) is not None,'准入客机路径非法')
    require(tag in ('g1','p1','p2'),'准入组名非法')
    remote=target+'/'+tag+'-admission.tar.gz'
    return remote,(f"test ! -e {remote} && tar --exclude='{tag}/aux' "
                   f"--exclude='{tag}/回放前辅助文件' -czf {remote} -C {target} {tag}")


def extract(archive,destination,tag='g1'):
    require(tag in ('g1','p1','p2'),'准入组名非法')
    require(not (destination/tag).exists(),'不覆盖准入证据目录')
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        for member in members:
            path=Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts and
                path.parts and path.parts[0]==tag and len(path.parts)<=2 and
                (member.isfile() or (member.isdir() and path.parts==(tag,))),
                '准入包出现非直接文件、链接或越界路径')
        root=destination/tag;root.mkdir()
        for member in members:
            if member.isdir():continue
            path=root/Path(member.name).name
            with tar.extractfile(member) as source,path.open('xb') as out:
                while data:=source.read(1<<20):out.write(data)
    return root


def collect(run,destination,tag='g1'):
    remote,command=archive_command(run.target,tag)
    run.ssh(command,120)
    expected=run.ssh('sha256sum '+remote).split()[0]
    archive=destination/(tag+'-admission.tar.gz')
    run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(archive)],180)
    require(digest(archive)==expected,'准入证据传输哈希不符')
    extract(archive,destination,tag)
