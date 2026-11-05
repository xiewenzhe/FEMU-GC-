#!/usr/bin/env python3
"""只在失败镜像新子镜像中读取旧内核日志；不恢复或声称恢复内存NAND。"""
import argparse
import getpass
import json
import signal
import shutil

from run_ycsb_af_host import SuiteRun, PROJECT, IMAGE_DIR, host_resources
from ycsb_af import digest, require

SOURCE = IMAGE_DIR/'joint-ycsb32-af-v3-20261023-results'
FAILED = IMAGE_DIR/'joint-ycsb32-af-v3-20261023-full-a-space.qcow2'
LABEL = 'joint-ycsb32-transport-diag-v1-20261023'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    args.label = LABEL
    profile = json.loads((SOURCE/'平台配置.json').read_text())
    require(FAILED.is_file(), '失败镜像不存在')
    output = IMAGE_DIR/(LABEL+'-results')
    require(not output.exists() and not (IMAGE_DIR/(LABEL+'-diag.qcow2')).exists(), '诊断目录已存在，拒绝覆盖')
    before = host_resources(profile)
    if args.check:
        print(json.dumps(before, ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('只读历史内核诊断认证（仅内存）：')
    output.mkdir()
    shutil.copytree(SOURCE/'源码', output/'源码')
    run = SuiteRun(args, password, output, profile)
    run.parent_image = FAILED
    run.qemu_digest = digest(PROJECT/'build-femu/qemu-system-x86_64')
    out = output/'diag'
    out.mkdir()
    run.record(阶段='从失败镜像新子镜像只读调查；原镜像不修改', 诊断父镜像=str(FAILED))
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        run.start('diag')
        command = r'''printf '旧内核OOM日志\n'
for log in /var/log/kern.log /var/log/kern.log.1 /var/log/syslog; do
  if test -f "$log"; then printf '\n文件：%s\n' "$log"; rg -n -C 3 'oom-kill|Out of memory|Killed process|Memory cgroup out of memory|python3|sshd' "$log" | tail -110; fi
done
printf '\n持久化日志启动列表\n'
journalctl --list-boots --no-pager | tail -6
printf '\n上次启动内核记录\n'
journalctl -k -b -1 --no-pager | tail -100
printf '\n客机资源\n'
free -h
df -h /
'''
        raw = run.root(command, 60)
        (output/'历史内核诊断.log').write_text(raw)
        print(raw, flush=True)
    finally:
        # 这里只需归档已部署诊断主体；没有业务NAND状态或新业务结果。
        run.finish(out)
        run.password = None
    run.record(阶段='历史内核证据已归档，诊断客机已正常关闭', 当前客机PID=None)


if __name__ == '__main__':
    main()
