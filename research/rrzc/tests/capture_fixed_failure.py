#!/usr/bin/env python3
"""只诊断本轮精确程序；读取内核栈，已有gdb时额外有界采用户栈，不安装工具。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import argparse

PROGRAM = "/home/femu/rrzc-guarded-medium-20261015-guarded-tests/stream_rrzc_test"


def read(path):
    try:
        return path.read_text()
    except (OSError, UnicodeError) as error:
        return "读取失败："+str(error)


def matches(proc, program=PROGRAM):
    try:
        args = (proc/"cmdline").read_bytes().split(b"\0")
        return args[0] == program.encode() and (proc/"exe").resolve() == Path(program)
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy",choices=("guarded","greedy"),default="guarded")
    strategy = parser.parse_args().strategy
    program = f"/home/femu/rrzc-guarded-medium-20261015-{strategy}-tests/stream_rrzc_test"
    if os.geteuid() != 0:
        raise SystemExit("仅本轮隔离客机root可读取线程栈")
    result = dict(说明="内核栈与用户栈分别记录；无gdb时明确缺失，不将wchan当用户栈。",进程=[])
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or not matches(proc,program):
            continue
        item = dict(PID=int(proc.name),状态=read(proc/"status"),线程=[])
        for task in sorted((proc/"task").iterdir()):
            item["线程"].append(dict(TID=int(task.name),状态=read(task/"status"),
                等待点=read(task/"wchan"),内核栈=read(task/"stack"),系统调用=read(task/"syscall")))
        debugger = shutil.which("gdb")
        item["用户栈"] = "未安装gdb，未取得用户栈"
        if debugger and matches(proc,program):
            try:
                run = subprocess.run([debugger,"-q","-nx","-batch","-ex","set pagination off",
                    "-ex","thread apply all bt","-ex","detach","-p",proc.name],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15)
                item["用户栈"] = dict(退出码=run.returncode,输出=run.stdout)
            except subprocess.TimeoutExpired as error:
                item["用户栈"] = dict(状态="gdb超时，诊断子进程已回收，未发送业务终止信号",
                    部分输出=(error.stdout or b"").decode(errors="replace") if isinstance(error.stdout,bytes) else error.stdout)
        result["进程"].append(item)
    kernel = subprocess.run(["dmesg","--ctime"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10)
    result["内核日志末200行"] = kernel.stdout.splitlines()[-200:]
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
