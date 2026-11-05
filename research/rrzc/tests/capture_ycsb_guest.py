#!/usr/bin/env python3
"""只读观察业务；另存诊断副本，不修改NAND、业务结果、阈值或进程状态。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def active_log(result, temporary=Path('/tmp')):
    if (result/'zenfs.run.log').is_file():
        return result/'zenfs.run.log'
    before = set((result/'logs.before.txt').read_text().splitlines()) if (result/'logs.before.txt').exists() else set()
    logs = [p for p in temporary.glob('zenfs_nvme0n1_*.log') if str(p) not in before]
    if len(logs) != 1:
        raise RuntimeError('业务维护日志无法唯一定位，候选数='+str(len(logs)))
    return logs[0]


def progress(result):
    text = (result/'ycsb.log').read_text(errors='replace') if (result/'ycsb.log').exists() else ''
    rows = re.findall(r'(\d+) sec: (\d+) operations;', text)
    rock = result/'aux/rrzc-ycsb/LOG'
    errors = [s for s in rock.read_text(errors='replace').splitlines() if re.search(
        r'Background (compaction|flush) error|Zone allocation failure|Corruption:', s)] if rock.exists() else []
    point = dict(完成=(result/'finished').exists(), 装载完成='Load runtime(sec):' in text,
                 最近状态=rows[-1] if rows else None, 错误=errors[-3:])
    try:
        log = active_log(result)
        zen = log.read_text(errors='replace')
        point.update(维护日志=str(log), RR开始=zen.count('RR 开始：'), RR完成=zen.count('RR 完成：'),
                     ZC完成=zen.count('ZC 统计：'), 分配等待登记=zen.count('空间紧急分配等待：'))
    except (OSError, RuntimeError) as error:
        point['维护观察未知'] = str(error)
    return point


def thread_state(target, proc=Path('/proc')):
    rows = []
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process/'exe').resolve() != target/'ycsb/ycsb':
                continue
            item = dict(PID=int(process.name), 线程=[])
            for task in sorted((process/'task').iterdir()):
                row = dict(TID=int(task.name))
                for name in ('comm', 'wchan', 'stack'):
                    try:
                        row[name] = (task/name).read_text()[:8192]
                    except OSError as error:
                        row[name+'未知'] = type(error).__name__
                item['线程'].append(row)
            rows.append(item)
        except OSError:
            continue
    return rows


def capture(result, output, target, threads_only=False, temporary=Path('/tmp')):
    output.mkdir(parents=True, exist_ok=False)
    report = dict(开始UTC=datetime.now(timezone.utc).isoformat(), 完整=False, 错误=[], 文件={},
                  业务包装已结束=(result/'finished').exists())
    (output/'线程.json').write_text(json.dumps(thread_state(target), ensure_ascii=False, indent=2))
    if not threads_only:
        sources = [('ycsb.partial.log', result/'ycsb.log'), ('rocksdb.partial.log', result/'aux/rrzc-ycsb/LOG')]
        try:
            sources.append(('zenfs.partial.log', active_log(result, temporary)))
        except (OSError, RuntimeError) as error:
            report['错误'].append(str(error))
        for name, source in sources:
            try:
                started = time.time_ns()
                shutil.copyfile(source, output/name)
                report['文件'][name] = dict(源=str(source), 复制开始纳秒=started, 复制结束纳秒=time.time_ns(),
                    字节=(output/name).stat().st_size, SHA256=hashlib.sha256((output/name).read_bytes()).hexdigest())
            except OSError as error:
                report['错误'].append(name+'：'+str(error))
        for name, command in (
            ('device.snapshot.json', [str(target/'sst_device_probe'), str(output/'device.probe.log')]),
            ('zones.snapshot.json', ['nvme', 'zns', 'report-zones', '/dev/nvme0n1', '-o', 'json'])):
            try:
                reply = subprocess.run(command, capture_output=True, text=True, timeout=8)
                (output/name).write_text(reply.stdout)
                if reply.returncode:
                    raise RuntimeError('退出='+str(reply.returncode)+'，'+reply.stderr[-1000:])
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                report['错误'].append(name+'：'+str(error))
    report.update(完整=not report['错误'], 结束UTC=datetime.now(timezone.utc).isoformat(),
                  边界='各文件复制不是原子快照；只表示采证时可读的日志和设备状态，不是完整NAND快照或最终业务验收')
    (output/'采证.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('progress', 'capture', 'threads'))
    parser.add_argument('result', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.result.parent != Path('/home/femu/rrzc-sst-results') or not re.fullmatch(r'(smoke|short|long)-(greedy|stable)', args.result.name):
        parser.error('只允许本项目明确登记的业务结果路径')
    if args.action == 'progress':
        print(json.dumps(progress(args.result), ensure_ascii=False))
    else:
        if os.geteuid() != 0 or args.output is None:
            parser.error('线程/设备采证需要客机root及独立输出目录')
        report = capture(args.result, args.output, args.target, args.action == 'threads')
        print(json.dumps(report, ensure_ascii=False))
        if not report['完整']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
