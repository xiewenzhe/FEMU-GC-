#!/usr/bin/env python3
"""仅采集精确YCSB现场；不发送信号、不附加调试器、不写进程内存。"""
import json
import os
from pathlib import Path
import re
import struct

PROGRAM='/home/femu/rrzc-ycsb-retention-20261017-tests/ycsb/ycsb'
PROPERTIES='/home/femu/rrzc-ycsb-retention-20261017-tests/nowal-20261017/workloada-zns-nowal.properties'
RESULT=Path('/home/femu/rrzc-ycsb-results/20261017')

def read(path):
    try:return path.read_text()
    except (OSError,UnicodeError) as e:return '读取失败：'+str(e)

def capture():
    if os.geteuid()!=0:raise RuntimeError('仅客机root可读取这个隔离试次的线程现场')
    expected=[PROGRAM,'-load','-run','-db','rocksdb','-threads','1','-P',PROPERTIES]
    matches=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            args=(proc/'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
            if args==expected and str((proc/'exe').resolve())==PROGRAM:matches.append(proc)
        except (OSError,UnicodeError):continue
    if len(matches)!=1:raise RuntimeError('不是唯一且精确匹配的本轮业务进程')
    proc=matches[0];maps=read(proc/'maps');regions=[]
    for row in maps.splitlines():
        p=row.split(maxsplit=5)
        if len(p)==6 and p[5]==PROGRAM and 'x' in p[1]:
            lo,hi=(int(x,16) for x in p[0].split('-'));regions.append((lo,hi,int(p[2],16)))
    with Path(PROGRAM).open('rb') as f:elf_type=struct.unpack_from('<H',f.read(18),16)[0]
    memory=os.open(str(proc/'mem'),os.O_RDONLY)
    def mem(address,size):
        try:return os.pread(memory,size,address)
        except OSError:return b''
    out=dict(PID=int(proc.name),参数=expected,映射=maps,ELF类型=elf_type,线程=[])
    tasks=sorted((proc/'task').iterdir(),key=lambda p:int(p.name))
    for task in tasks:
        syscall=read(task/'syscall');p=syscall.split()
        item=dict(TID=int(task.name),名称=read(task/'comm').strip(),等待点=read(task/'wchan'),
                  内核栈=read(task/'stack'),系统调用=syscall,栈内代码地址=[])
        if len(p)>=9 and p[0]=='202':
            address=int(p[1],0);raw=mem(address,40)
            item['futex地址']=hex(address);item['futex操作']=int(p[2],0)
            item['futex预期值']=int(p[3],0)
            item['地址处十个32位字']=list(struct.unpack('<10I',raw)) if len(raw)==40 else []
            item['说明']='仅当用户代码和等待类型确认是pthread_mutex时，偏移8的字段才可解释为owner；条件变量不可照搬。'
        if len(p)>=9 and p[0].lstrip('-').isdigit():
            stack=int(p[-2],0);raw=mem(stack,32768);seen=set()
            for offset in range(0,len(raw)-7,8):
                address=struct.unpack_from('<Q',raw,offset)[0]
                for lo,hi,file_offset in regions:
                    if lo<=address<hi and address not in seen:
                        seen.add(address)
                        item['栈内代码地址'].append(dict(栈偏移=offset,运行地址=hex(address),
                            ELF地址=hex(address if elf_type==2 else address-lo+file_offset)))
                        break
                if len(seen)>=80:break
        out['线程'].append(item)
    os.close(memory)
    before=set((RESULT/'logs.before.txt').read_text().splitlines())
    logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in before]
    if len(logs)!=1:raise RuntimeError('当前业务日志不是唯一新增文件')
    log=logs[0];out['业务日志']=str(log);out['日志字节']=log.stat().st_size
    markers=re.compile(r'GC 检查：|ZC 统计：|RR (开始|完成|统计)：|独立约束决策：|独立约束维护结果：|MigrateFileExtents|V1后续迁移复制：|V1后续映射提交：|分配返回空|ReleaseMigrateZone|Reopen writable|\[ERROR\]')
    selected=[]
    with log.open(errors='replace') as f:
        for number,line in enumerate(f,1):
            if markers.search(line):selected.append(dict(行号=number,原文=line.rstrip()))
    out['最近关键日志']=selected[-60:]
    out['边界']='栈扫描得到的代码地址不是完整用户回溯；可能含历史栈残留，须结合futex所有者与实际代码验证。'
    return out

if __name__=='__main__':print(json.dumps(capture(),ensure_ascii=False,indent=2))
