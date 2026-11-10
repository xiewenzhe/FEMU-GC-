"""检查放置时已完成的LSM快照可见性；区间相交不是覆盖更新或退役预测。"""
import argparse
from bisect import bisect_right
import csv
import hashlib
import json
from pathlib import Path
import re
import struct

from analyze_placement_trace import require

MODES = ('G2', 'G2_WSURVIVOR')


def parse_key(text):
    value=bytes.fromhex(text)
    require(len(value)==16 and value[8:]==b'00000000', '本轮Key编码不符')
    return int.from_bytes(value[:8], 'big')


def snapshot(path):
    with path.open(encoding='utf-8-sig') as stream:
        return {int(Path(row['文件']).stem):dict(level=int(row['层级']),
            low=parse_key(row['最小键十六进制']), high=parse_key(row['最大键十六进制']),
            size=int(row['字节'])) for row in csv.DictReader(stream)}


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def analyze(pair, history, output):
    require(not output.exists(), '不覆盖已保存分析')
    with (history/'历史前缀逐次回放.csv').open(encoding='utf-8-sig') as stream:
        queries=[r for r in csv.DictReader(stream) if r['诊断窗口秒']=='30']
    results, evidence, groups=[], {}, {}
    for mode in MODES:
        arm=pair/mode
        raw=arm/json.loads((arm/'状态.json').read_text())['标签']/'p1'
        report=json.loads((arm/'业务核验.json').read_text())
        run_log=(raw/'run.log').read_text()
        names=dict((int(f),int(Path(name).stem)) for f,name in re.findall(
            r'Flush来源W标记：文件编号=(\d+)，文件名=([^，]+)，',run_log))
        targets={r['事件']:r for r in report['组成']['目标'] if r['类型']=='实际目标'}
        data=(raw/'等待区间.bin').read_bytes()
        require(data[:16]==b'RRZCTM01'+b'\x00'*8, '计时trace缺帧或格式不符')
        intervals=sorted((r for r in struct.iter_unpack('<6Q', data[16:]) if r[0]==4),
                         key=lambda r:r[1])
        marks=[tuple(map(int, x)) for x in re.findall(
            r'SST生命周期阶段：序号=(\d+).*?成功请求=(\d+)，纳秒=(\d+)',
            run_log)]
        require(len(intervals)==len(marks)==120, '120次业务布局与计时不符')
        require([x[0] for x in marks]==list(range(1,121)), '布局序号不连续')
        for interval,mark in zip(intervals,marks):
            require(interval[2]<=mark[2] and interval[5]==mark[1], '布局时点与请求进度未关联')
        # 原入口先rrzclayout一次，再执行120次checkpoint；最后的收尾快照不用作过去输入。
        ends=[x[2] for x in intervals]
        snapshots={i:snapshot(raw/f'布局-{i}.sst.csv') for i in range(1,121)}
        files=[raw/'等待区间.bin', raw/'run.log', raw/'run.rocksdb.log', arm/'业务核验.json',
               *(raw/f'布局-{i}.sst.csv' for i in range(1,121))]
        evidence[mode]={str(p):digest(p) for p in files}
        for query in (r for r in queries if r['模式']==mode):
            event=int(query['目标事件']);now=int(query['查询单调纳秒'])
            index=bisect_right(ends, now)-1
            chosen=index+1 if index>=0 else None
            fid=int(query['W文件']);target=targets[event]
            row=dict(模式=mode, 目标事件=event, 路径=query['路径'], W文件=fid,
                Zone编号=query['Zone编号'], 代际=query['代际'],
                查询纳秒=now, 最近已完成快照=chosen,
                快照结束纳秒=ends[index] if chosen else None,
                快照年龄秒=(now-ends[index])/1e9 if chosen else None,
                W在过去快照可见=False, 当时Level快照=None,
                最小Key=None, 最大Key=None, 同级相交文件数=None, 上层相交文件数=None,
                下层相交文件数=None, 上层相交文件编号=None,
                当前目标W字节=target['当前组成']['W_only'],
                普通备选即时记录='未采集；实际已取得目标不能代替未执行路径',
                历史判断=query['历史判断'], 实际30秒内退役=query['实际窗口内退役'])
            # Flush来源日志显式给出ZenFS ID到原生文件名，避免跨层编号偏移假设。
            native=names[fid]
            if chosen and native in snapshots[chosen]:
                table=snapshots[chosen];w=table[native]
                intersect=[(number,x) for number,x in table.items() if number!=native
                           and w['low']<=x['high'] and x['low']<=w['high']]
                row.update(W在过去快照可见=True, 当时Level快照=w['level'],
                    最小Key=w['low'], 最大Key=w['high'],
                    同级相交文件数=sum(x['level']==w['level'] for _,x in intersect),
                    上层相交文件数=sum(x['level']<w['level'] for _,x in intersect),
                    下层相交文件数=sum(x['level']>w['level'] for _,x in intersect),
                    上层相交文件编号=';'.join(str(n) for n,x in intersect if x['level']<w['level']))
            results.append(row)
        current=[r for r in results if r['模式']==mode]
        groups[mode]=dict(关联数=len(current), 过去快照有该文件=sum(r['W在过去快照可见'] for r in current),
            按路径={route:dict(总数=sum(r['路径']==route for r in current),
                可见数=sum(r['路径']==route and r['W在过去快照可见'] for r in current))
                for route in sorted({r['路径'] for r in current})})
    output.mkdir(parents=True)
    with (output/'LSM前置快照可见性.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,list(results[0]));writer.writeheader();writer.writerows(results)
    (output/'原始证据索引.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    (output/'可见性核验.json').write_text(json.dumps(dict(各臂=groups,
        无未来快照=all(r['快照结束纳秒'] is None or r['快照结束纳秒']<=r['查询纳秒'] for r in results),
        边界='过去快照可能陈旧，不是实时Version；区间相交不等于Key覆盖；未观察普通路径反事实'),
        ensure_ascii=False,indent=2)+'\n')
    return groups


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('pair','history','output'):parser.add_argument(name,type=Path)
    args=parser.parse_args();print(json.dumps(analyze(args.pair,args.history,args.output),ensure_ascii=False,indent=2))
