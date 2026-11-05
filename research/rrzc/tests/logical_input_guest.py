#!/usr/bin/env python3
"""仅在独立8GiB FEMU客机保存逻辑输入并串行回放；不保存NAND或RC快照。"""
import ctypes
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from strict_stats_guest import Command

DEVICE='/dev/nvme0n1'
CHUNK=65536


def report():
    return json.loads(subprocess.check_output(['nvme','zns','report-zones',DEVICE,'-o','json']))['zone_list']


def guard():
    assert os.geteuid()==0 and 'FEMU' in Path('/sys/class/nvme/nvme0/model').read_text().upper(), '只允许隔离FEMU客机'
    assert int(subprocess.check_output(['blockdev','--getsize64',DEVICE]))==8<<30,'容量不符，拒绝操作'
    assert not subprocess.check_output(['lsblk','-nr','-o','MOUNTPOINTS',DEVICE]).strip(),'设备已挂载'
    zones=report()
    assert len(zones)==64 and all(r['cap']==262144 and r['slba']==i*262144 for i,r in enumerate(zones)), 'Zone几何不符'
    return zones


def shape(zones):
    return [(r['slba'],r['cap'],r['wp'],r['state']) for r in zones]


def length(zone):
    assert zone['state'] in ('EMPTY','FULL','CLOSED','IMP_OPENED','EXP_OPENED'),'不支持的状态'
    if zone['state']=='EMPTY':return 0
    return zone['cap']*512 if zone['state']=='FULL' else (zone['wp']-zone['slba'])*512


def io(fd,lba,size,payload=None):
    assert size>0 and size<=CHUNK and size%512==0
    data=ctypes.create_string_buffer(size)
    if payload is not None:
        assert len(payload)==size
        ctypes.memmove(data,payload,size)
    cmd=Command();cmd.opcode=2 if payload is None else 1;cmd.nsid=1
    cmd.addr=ctypes.addressof(data);cmd.data_len=size
    cmd.cdw10=lba&0xffffffff;cmd.cdw11=lba>>32;cmd.cdw12=size//512-1;cmd.timeout_ms=10000
    assert fcntl.ioctl(fd,0xc0484e43,bytearray(bytes(cmd)),True)==0,'串行逻辑读写失败'
    return data.raw if payload is None else None


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def capture(target,source):
    zones=guard();dest=target/'逻辑输入'
    assert not dest.exists(),'不覆盖已保存输入'
    # 按实际已写入量预留两份原始数据及2 GiB日志余量，避免固定12 GiB误拒较小的输入。
    required=2*sum(length(z) for z in zones)+(2<<30)
    available=shutil.disk_usage(target).free
    assert available>=required,f'客机剩余空间不足以保存输入及归档：可用{available}字节，需{required}字节'
    dest.mkdir();shutil.copytree(source/'aux',dest/'aux')
    for name in ('装载-0.sst.csv','装载-0.extent.csv'):
        shutil.copyfile(source/name,dest/name)
    rows=[];fd=os.open(DEVICE,os.O_RDONLY)
    try:
        for i,z in enumerate(zones):
            size=length(z);h=hashlib.sha256()
            if not size:continue
            path=dest/(str(i)+'.bin.gz')
            with gzip.open(path,'wb',compresslevel=1) as f:
                for offset in range(0,size,CHUNK):
                    data=io(fd,z['slba']+offset//512,min(CHUNK,size-offset));f.write(data);h.update(data)
            rows.append(dict(Zone=i,字节=size,SHA256=h.hexdigest(),压缩SHA256=hashlib.sha256(path.read_bytes()).hexdigest()))
            print('保存固定逻辑输入：Zone='+str(i)+'，字节='+str(size),flush=True)
    finally:os.close(fd)
    assert shape(report())==shape(zones),'保存时Zone状态发生变化'
    dump(dest/'清单.json',dict(Zone=zones,逐区=rows,辅助路径=str(source/'aux'),
        边界='逻辑盘字节和Zone状态输入；不保存物理映射、RC、设备队列或时钟，不称NAND快照'))


def replay(target,result):
    zones=guard();dest=target/'逻辑输入';manifest=json.loads((dest/'清单.json').read_text())
    # 所有压缩输入先验哈希；破坏性Reset仅对上述强校验的独立模拟盘执行。
    for row in manifest['逐区']:
        assert hashlib.sha256((dest/(str(row['Zone'])+'.bin.gz')).read_bytes()).hexdigest()==row['压缩SHA256'],'输入哈希不符'
    subprocess.run(['nvme','zns','reset-zone',DEVICE,'-a'],check=True)
    zones=guard();assert all(z['state']=='EMPTY' and z['wp']==z['slba'] for z in zones),'Reset未生效'
    oldaux=Path(manifest['辅助路径'])
    assert oldaux==target/'source/aux' and oldaux.is_dir(),'辅助路径不属于固定输入'
    if (target/'启用辅助目录tmpfs').exists():
        from aux_tmpfs_guest import restore_aux
        restore_aux(target,result,dest/'aux',oldaux)
    else:
        assert not oldaux.is_symlink(),'普通回放不允许辅助路径为符号链接'
        oldaux.rename(result/'回放前辅助文件');shutil.copytree(dest/'aux',oldaux)
    (result/'aux').symlink_to(oldaux,target_is_directory=True)
    rows=sorted(manifest['逐区'],key=lambda r:(manifest['Zone'][r['Zone']]['state']!='FULL',r['Zone']))
    sequence=hashlib.sha256();checks=[];fd=os.open(DEVICE,os.O_RDWR)
    try:
        for row in rows:
            z=manifest['Zone'][row['Zone']];size=row['字节'];h=hashlib.sha256();offset=0
            with gzip.open(dest/(str(row['Zone'])+'.bin.gz'),'rb') as f:
                while offset<size:
                    data=f.read(min(CHUNK,size-offset));assert data and len(data)%512==0,'输入长度异常'
                    lba=z['slba']+offset//512;io(fd,lba,len(data),data)
                    sequence.update(f'{lba}:{len(data)}\n'.encode());h.update(data);offset+=len(data)
                assert not f.read(1),'输入尾部长度异常'
            assert h.hexdigest()==row['SHA256'],'解压内容哈希不符'
            state=z['state']
            if state in ('CLOSED','EXP_OPENED'):
                action='close-zone' if state=='CLOSED' else 'open-zone'
                subprocess.run(['nvme','zns',action,DEVICE,'--start-lba='+str(z['slba'])],check=True)
            checks.append(dict(Zone=row['Zone'],字节=offset,SHA256=h.hexdigest()))
            print('串行回放固定逻辑输入：Zone='+str(row['Zone'])+'，字节='+str(offset),flush=True)
    finally:os.close(fd)
    assert shape(report())==shape(manifest['Zone']),'回放WP或状态不符'
    for name in ('装载-0.sst.csv','装载-0.extent.csv'):
        shutil.copyfile(dest/name,result/name)
    dump(result/'逻辑输入回放.json',dict(通过=True,串行写命令摘要=sequence.hexdigest(),逐区=checks,
        输入清单SHA256=hashlib.sha256((dest/'清单.json').read_bytes()).hexdigest(),
        边界=manifest['边界']))


def wrappers(base):
    from calibration_source import once
    source=base.split('python3 "$target/calibration_guest.py" warmup',1)[0]
    assert source!=base,'固定输入要求真实RC准备入口'
    source+='python3 "$target/logical_input_guest.py" capture "$target" "$result" >> "$result/load.log" 2>&1\nprintf "done\\n" > "$result/phase.txt"\n'
    replayed=once(base,'mkdir "$result" "$result/aux"','mkdir "$result"')
    start=replayed.index('nvme zns reset-zone');end=replayed.index('enabled=0;',start)
    replayed=replayed[:start]+'''printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler
printf 'load\\n' > "$result/phase.txt"
python3 "$target/logical_input_guest.py" replay "$target" "$result" >> "$result/load.log" 2>&1
cd /home/femu/rocksdb
'''+replayed[end:]
    start=replayed.index('hotspot=1;layout_prefix=$result/装载;phase load')
    end=replayed.index('python3 "$target/calibration_guest.py" warmup',start)
    replayed=replayed[:start]+'hotspot=1;snapshot before\n'+replayed[end:]
    # 每轮恢复相同辅助目录，日志必须独立复制，不能让后续操作修改此前归档。
    replayed=once(replayed,'import os,sys','import os,sys,shutil')
    replayed=once(replayed,"os.link(p,r/(phase+'.rocksdb.log'))","shutil.copyfile(p,r/(phase+'.rocksdb.log'))")
    return source,replayed


if __name__=='__main__':
    if sys.argv[1]=='capture':capture(Path(sys.argv[2]),Path(sys.argv[3]))
    elif sys.argv[1]=='replay':replay(Path(sys.argv[2]),Path(sys.argv[3]))
    else:raise ValueError('未知逻辑输入操作')
