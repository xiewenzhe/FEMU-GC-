#!/usr/bin/env python3
"""本轮流式只读采证主体：增量读取、分块哈希，避免在4GiB客机物化GB级Unicode日志。"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import time

BLOCK = 1 << 16
MAX_LINE = 1 << 20
MARKERS = {k: v.encode() for k, v in dict(RR开始='RR 开始：', RR完成='RR 完成：',
           ZC完成='ZC 统计：', 分配等待登记='空间紧急分配等待：').items()}
ERROR = re.compile(rb'Background (compaction|flush) error|Zone allocation failure|Corruption:')


def active_log(result, temporary=Path('/tmp'), *, allow_missing=False):
    if (result/'zenfs.run.log').is_file():
        return result/'zenfs.run.log'
    before = set((result/'logs.before.txt').read_text().splitlines()) if (result/'logs.before.txt').exists() else set()
    logs = [p for p in temporary.glob('zenfs_nvme0n1_*.log') if str(p) not in before]
    if not logs and allow_missing:
        return None
    if len(logs) != 1:
        raise RuntimeError('业务维护日志无法唯一定位，候选数='+str(len(logs)))
    return logs[0]


def incremental(path, state, consume):
    if not path.exists():
        if state:
            raise RuntimeError('已登记日志消失：'+str(path))
        return 0
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        if state and (state['身份'] != identity or stat.st_size < state['偏移']):
            raise RuntimeError('日志被替换或截断，不能静默清零累计计数：'+str(path))
        if not state:
            state.update(身份=identity, 偏移=0, 尾部='')
        end, offset = stat.st_size, state['偏移']
        stream.seek(offset)
        tail = bytes.fromhex(state['尾部'])
        scanned = 0
        while stream.tell() < end:
            block = stream.read(min(BLOCK, end-stream.tell()))
            if not block:
                raise RuntimeError('日志读取时意外缩短：'+str(path))
            scanned += len(block)
            data = tail+block
            split = data.rfind(b'\n')
            if split >= 0:
                consume(data[:split+1])
                tail = data[split+1:]
            else:
                tail = data
            if len(tail) > MAX_LINE:
                raise RuntimeError('单行日志超过1MiB，拒绝无界内存采样')
        state.update(偏移=stream.tell(), 尾部=tail.hex())
        return scanned


def progress(result, *, cache=None, temporary=Path('/tmp')):
    # 只约束只读采样进程，不修改YCSB、FEMU或系统内存配置。
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = min(256 << 20, hard if hard != resource.RLIM_INFINITY else 256 << 20)
    if soft == resource.RLIM_INFINITY or soft > limit:
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    result = Path(result)
    cache = cache or Path(__file__).with_name('增量进度-'+result.name+'.json')
    state = json.loads(cache.read_text()) if cache.exists() else dict(
        版本=1, 结果目录=str(result), 文件={}, 装载完成=False, 最近状态=None, 错误=[],
        **{k: 0 for k in MARKERS})
    if state.get('版本') != 1 or state.get('结果目录') != str(result):
        raise RuntimeError('增量缓存版本或结果目录不符')
    scanned = 0
    def read(path, consume):
        nonlocal scanned
        key = str(path)
        if key not in state['文件'] and path.exists():
            stat = path.stat()
            same = [s for s in state['文件'].values() if s and s['身份'] == [stat.st_dev, stat.st_ino]]
            if same:
                # 包装器结束后zenfs.run.log为同一源日志硬链接，不从头重复累计。
                state['文件'][key] = dict(same[0])
        scanned += incremental(path, state['文件'].setdefault(str(path), {}), consume)
    def ycsb(data):
        if b'Load runtime(sec):' in data:
            state['装载完成'] = True
        rows = re.findall(rb'(\d+) sec: (\d+) operations;', data)
        if rows:
            state['最近状态'] = [s.decode() for s in rows[-1]]
    def rocks(data):
        for line in data.splitlines():
            if ERROR.search(line):
                state['错误'] = (state['错误']+[line.decode(errors='replace')])[-3:]
    def zenfs(data):
        for key, token in MARKERS.items():
            state[key] += data.count(token)
    read(result/'ycsb.log', ycsb)
    read(result/'aux/rrzc-ycsb/LOG', rocks)
    finished = (result/'finished').exists()
    allow_missing = not (state.get('维护日志已登记', False) or state['装载完成'] or finished)
    # 包装器先核验软件再创建logs.before；此时既不能报日志丢失，也不能误读mkfs旧日志。
    if not (result/'logs.before.txt').exists():
        if not allow_missing:
            raise RuntimeError('业务已启动或已结束，但缺少日志起点清单')
        log = None
    else:
        log = active_log(result, temporary, allow_missing=allow_missing)
    if log is not None:
        read(log, zenfs)
        state['维护日志已登记'] = True
    point = {k: state[k] for k in ('装载完成', '最近状态', '错误', *MARKERS)}
    point.update(完成=finished, 维护日志=str(log) if log is not None else None,
                 维护日志状态='已建立' if log is not None else '等待业务日志建立',
                 采样本次读取字节=scanned, 采样峰值RSSKiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # 仅本轮独立采证目录缓存偏移与小尾部，不修改业务日志或设备。
    next_file = cache.with_suffix('.tmp')
    next_file.write_text(json.dumps(state, ensure_ascii=False))
    next_file.replace(cache)
    return point


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


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
                  业务包装已结束=(result/'finished').exists(), 版本=2, 哈希方式='1MiB分块，不整文件读取')
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
                # finished之后业务日志不再写入：同盘硬链接保留完整字节，不重复占根盘。
                link = report['业务包装已结束'] and source.stat().st_dev == output.stat().st_dev
                if link:
                    os.link(source, output/name)
                else:
                    shutil.copyfile(source, output/name)
                report['文件'][name] = dict(源=str(source), 复制开始纳秒=started, 复制结束纳秒=time.time_ns(),
                    字节=(output/name).stat().st_size, SHA256=digest(output/name),
                    保存方式='已关闭日志硬链接' if link else '异常时另存副本')
            except OSError as error:
                report['错误'].append(name+'：'+str(error))
        commands = [('device.snapshot.json', [str(target/'sst_device_probe'), str(output/'device.probe.log')]),
                    ('zones.snapshot.json', ['nvme', 'zns', 'report-zones', '/dev/nvme0n1', '-o', 'json']),
                    ('kernel.log', ['journalctl', '-k', '-n', '160', '--no-pager']),
                    ('memory.log', ['free', '-h']), ('root-space.log', ['df', '-h', '/'])]
        for name, command in commands:
            try:
                reply = subprocess.run(command, capture_output=True, text=True, timeout=8)
                (output/name).write_text(reply.stdout+reply.stderr)
                if reply.returncode:
                    raise RuntimeError('退出='+str(reply.returncode)+'，'+reply.stderr[-1000:])
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                report['错误'].append(name+'：'+str(error))
    report.update(完整=not report['错误'], 结束UTC=datetime.now(timezone.utc).isoformat(),
                  边界='采证不是原子NAND快照；结束前副本可不同时，结束后硬链接源必须保持只读')
    (output/'采证.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
