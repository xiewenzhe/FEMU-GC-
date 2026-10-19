#!/usr/bin/env python3
"""只读诊断固定锚点客机中的精确测试进程；不安装工具，不终止业务。"""
import json,os,shutil,subprocess
from pathlib import Path

DIRECTORY=Path('/home/femu/rrzc-rocksdb-anchor-20261016-tests')
RESULT=Path('/home/femu/rrzc-stream-results/rocksdb-anchor-20261016')
PROGRAM=DIRECTORY/'rocksdb_observe_test'

def read(path):
    try:return path.read_text()
    except (OSError,UnicodeError) as error:return '读取失败：'+str(error)

def main():
    if os.geteuid()!=0:raise SystemExit('只允许本轮隔离客机root诊断')
    pid=int((RESULT/'worker.pid').read_text());proc=Path('/proc')/str(pid)
    args=(proc/'cmdline').read_bytes().split(b'\0')
    if args[:3]!=[str(PROGRAM).encode(),b'run',str(RESULT).encode()] or (proc/'exe').resolve()!=PROGRAM:
        raise SystemExit('PID不是本轮精确测试程序，拒绝操作')
    out=dict(PID=pid,业务仍未结束=not (RESULT/'run.exit').exists(),状态=read(proc/'status'),线程=[])
    for task in sorted((proc/'task').iterdir()):
        out['线程'].append(dict(TID=int(task.name),等待点=read(task/'wchan'),内核栈=read(task/'stack'),系统调用=read(task/'syscall')))
    out['用户栈']='当前没有gdb，不安装工具'
    if shutil.which('gdb'):
        try:
            r=subprocess.run(['gdb','-q','-nx','-batch','-ex','set pagination off','-ex','thread apply all bt',
                '-ex','detach','-p',str(pid)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
            out['用户栈']=dict(退出码=r.returncode,输出=r.stdout)
        except subprocess.TimeoutExpired as error:
            out['用户栈']=dict(说明='诊断子进程超时回收，没有发送业务终止信号',
                部分输出=error.stdout.decode(errors='replace') if isinstance(error.stdout,bytes) else error.stdout)
    out['诊断后状态']=read(proc/'status')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
