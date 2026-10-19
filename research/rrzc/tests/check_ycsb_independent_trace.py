"""逐条复核真实请求：完整装载、热读边界、全空间更新及实际交集。"""
import json
from pathlib import Path
import struct
import sys
from collections import Counter


def check(path,records,hot,operations):
    if not (2<=hot<=records and operations>0):raise ValueError('请求规模不合法')
    inserts=0;reads=Counter();updates=Counter()
    with Path(path).open('rb') as stream:
        while op:=stream.read(1):
            size=stream.read(4)
            if len(size)!=4:raise ValueError('请求长度字段截断')
            length=struct.unpack('<I',size)[0]
            if length!=24:raise ValueError('不是user加20位补零有序键')
            raw=stream.read(length);value=stream.read(8)
            if len(raw)!=length or len(value)!=8:raise ValueError('请求内容截断')
            key=raw.decode('ascii')
            if not key.startswith('user') or not key[4:].isdigit():raise ValueError('键格式错误')
            number=int(key[4:])
            if number>=records:raise ValueError('请求超出已装载键空间')
            if op==b'I':
                if reads or updates or number!=inserts:raise ValueError('装载顺序或位置错误')
                inserts+=1
            elif inserts!=records:raise ValueError('装载尚未完成即开始业务')
            elif op==b'R':
                if number>=hot or value!=bytes(8):raise ValueError('读取超出热区或读轨迹哈希不为零')
                reads[number]+=1
            elif op==b'U':updates[number]+=1
            else:raise ValueError('出现未登记操作')
    if inserts!=records or sum(reads.values())+sum(updates.values())!=operations:
        raise ValueError('装载或业务请求总数不符')
    hot_updates=sum(v for k,v in updates.items() if k<hot)
    if not reads or not hot_updates or hot_updates==sum(updates.values()):
        raise ValueError('没有同时观察到热区读取、热区更新和热区外更新')
    return dict(装载数=inserts,读取数=sum(reads.values()),更新数=sum(updates.values()),
        读取不同键数=len(reads),更新不同键数=len(updates),热区更新数=hot_updates,
        最热读键=reads.most_common(5),说明='这是实际请求层分布，不保证物理块或Zone共置')


if __name__=='__main__':
    print(json.dumps(check(sys.argv[1],*map(int,sys.argv[2:])),ensure_ascii=False,indent=2))
