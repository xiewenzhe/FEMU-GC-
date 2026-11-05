#!/usr/bin/env python3
"""db_bench增量观测：真实操作计数与维护计数分开，异常保留非原子诊断副本。"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import time
from capture_ycsb_stream_guest import incremental, MARKERS, ERROR


def active(result, phase):
    saved = result/(phase+'.zenfs.log')
    if saved.exists():
        return saved
    before = result/(phase+'.logs.before.txt')
    if not before.exists():
        return None
    old = set(before.read_text().splitlines())
    choices = [p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]
    if not choices:
        return None
    if len(choices) != 1:
        raise RuntimeError('不能唯一定位当前阶段ZenFS日志')
    return choices[0]


def progress(result):
    resource.setrlimit(resource.RLIMIT_AS, (256<<20, 256<<20))
    cache = result/'增量观测.json'
    state = json.loads(cache.read_text()) if cache.exists() else dict(文件={}, 进度={}, 错误=[],
        **{k:0 for k in MARKERS})
    phase = (result/'phase.txt').read_text().strip() if (result/'phase.txt').exists() else '准备'
    def read(path, consume):
        incremental(path, state['文件'].setdefault(str(path), {}), consume)
    for name in ('load', 'run', 'verify'):
        def bench(data, name=name):
            for thread, count in re.findall(rb'thread (\d+): \(\d+,(\d+)\) ops', data):
                state['进度'][name+'-线程'+thread.decode()] = int(count)
            for line in data.splitlines():
                if b'error:' in line.lower() or b'Corruption:' in line:
                    state['错误'] = (state['错误']+[line.decode(errors='replace')])[-5:]
        read(result/(name+'.log'), bench)
        log = active(result, name) if phase == name or (result/(name+'.zenfs.log')).exists() else None
        if log is not None:
            # 日志闭合后只是同inode硬链接，不能重复累计。
            stat = log.stat()
            current = state['文件'].get(str(log))
            if not current:
                same = [v for v in state['文件'].values() if v and v['身份'] == [stat.st_dev, stat.st_ino]]
                if same:
                    state['文件'][str(log)] = dict(same[0])
            def zen(data):
                if name == 'verify':
                    state['校验RR完成'] = state.get('校验RR完成', 0)+data.count(MARKERS['RR完成'])
                    return
                for key, marker in MARKERS.items():
                    state[key] += data.count(marker)
                for line in data.splitlines():
                    if ERROR.search(line):
                        state['错误'] = (state['错误']+[line.decode(errors='replace')])[-5:]
            read(log, zen)
    # 原生多进程会旋转同名RocksDB LOG；只观察各阶段已关闭的独立日志。
    for name in ('load', 'run', 'verify'):
        def rocks(data):
            for line in data.splitlines():
                if ERROR.search(line):
                    state['错误'] = (state['错误']+[line.decode(errors='replace')])[-5:]
        read(result/(name+'.rocksdb.log'), rocks)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(cache)
    return dict(阶段=phase, 完成=(result/'finished').exists(), 进度=state['进度'],
                错误=state['错误'], **{k:state[k] for k in MARKERS})


def capture(result):
    phase = (result/'phase.txt').read_text().strip() if (result/'phase.txt').exists() else '准备'
    out = result/'异常采证'
    out.mkdir(exist_ok=False)
    sources = [result/(phase+'.log'), result/'aux/rrzc-dbbench/LOG']
    log = active(result, phase)
    if log is not None:
        sources.append(log)
    for index, source in enumerate(sources):
        if source.is_file():
            shutil.copyfile(source, out/(str(index)+'-'+source.name))
    (out/'说明.json').write_text(json.dumps(dict(阶段=phase,
        边界='运行中各文件顺序复制，不是原子NAND快照，不视为成功完成结果'), ensure_ascii=False))


def interrupt(result):
    path = result/'bench.pid'
    if not path.exists():
        return
    pid = int(path.read_text())
    proc = Path('/proc', str(pid))
    if not proc.exists():
        return
    if proc.stat().st_uid != 0 or (proc/'exe').resolve() != Path('/home/femu/rocksdb/db_bench') or \
            b'--db=/rrzc-dbbench' not in (proc/'cmdline').read_bytes().split(b'\0'):
        raise RuntimeError('中断目标PID、账号、可执行文件或数据库不符')
    os.kill(pid, signal.SIGINT)
    for _ in range(15):
        if not proc.exists():
            return
        time.sleep(1)
    raise RuntimeError('正常中断后业务仍存活；保留事实，后续由客机正常关机收尾')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('progress', 'capture', 'interrupt'))
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    if args.action == 'progress':
        print(json.dumps(progress(args.result), ensure_ascii=False))
    elif args.action == 'capture':
        capture(args.result)
    else:
        interrupt(args.result)
