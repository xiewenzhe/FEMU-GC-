"""独立复核生存先导实际请求；显式允许重叠，不放宽旧不相交验收器。"""
from collections import Counter
from pathlib import Path
import argparse
import json
import struct


def check(path, records, hot, operations, start, count):
    if not (2 <= hot <= records and 0 <= start < records and 2 <= count <= records-start):
        raise ValueError('登记键空间或更新范围不合法')
    inserts, reads, updates = 0, Counter(), Counter()
    with Path(path).open('rb') as stream:
        while op := stream.read(1):
            size = stream.read(4)
            if len(size) != 4 or struct.unpack('<I', size)[0] != 24:
                raise ValueError('请求键长度不符或截断')
            raw, value = stream.read(24), stream.read(8)
            if len(raw) != 24 or len(value) != 8:
                raise ValueError('请求内容截断')
            if not raw.startswith(b'user') or not raw[4:].isdigit():
                raise ValueError('请求键格式不符')
            key = int(raw[4:])
            if key >= records:
                raise ValueError('请求超出装载空间')
            if op == b'I':
                if reads or updates or key != inserts:
                    raise ValueError('装载不连续或业务后出现装载')
                inserts += 1
            elif inserts != records:
                raise ValueError('装载尚未完成即出现业务')
            elif op == b'R':
                if key >= hot or value != bytes(8):
                    raise ValueError('热读范围或读轨迹哈希不符')
                reads[key] += 1
            elif op == b'U':
                if not start <= key < start+count:
                    raise ValueError('更新超出登记范围')
                updates[key] += 1
            else:
                raise ValueError('出现非登记操作')
    if inserts != records or sum(reads.values())+sum(updates.values()) != operations:
        raise ValueError('实际请求总数不符')
    if not reads or not updates:
        raise ValueError('缺少读或更新')
    hot_updates = sum(n for key, n in updates.items() if key < hot)
    overlap = start < hot
    if (overlap and not hot_updates) or (not overlap and hot_updates):
        raise ValueError('实际热区更新与登记交集不符')
    return dict(装载数=inserts, 读取数=sum(reads.values()), 更新数=sum(updates.values()),
        热区更新数=hot_updates, 实际读写交集键数=len(set(reads)&set(updates)),
        读取不同键数=len(reads), 更新不同键数=len(updates),
        更新起点=start, 更新键数=count, 边界='仅逻辑请求交集，不代表物理Zone共置')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    for name in ('records', 'hot', 'operations', 'start', 'count'):
        parser.add_argument(name, type=int)
    args = parser.parse_args()
    print(json.dumps(check(args.path, args.records, args.hot, args.operations, args.start, args.count),
                     ensure_ascii=False, indent=2))
