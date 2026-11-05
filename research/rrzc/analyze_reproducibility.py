#!/usr/bin/env python3
"""只读比较Greedy重复实验；派生事件序号不是在线因果顺序，缺失证据不补造。"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import struct

from analyze_all_blocks_dbbench import batches, fields, maintenance
from analyze_calibration import background_overlap, timing

ROCK_FIELDS = {
    'flush_started': ('num_memtables', 'total_num_input_entries'),
    'flush_finished': ('lsm_state', 'immutable_memtables', 'output_compression'),
    'compaction_started': ('files_L0', 'files_L1', 'files_L2', 'files_L3', 'files_L4', 'files_L5', 'files_L6', 'compaction_reason'),
    'compaction_finished': ('output_level', 'num_output_files', 'total_output_size', 'num_input_records', 'num_output_records'),
    'table_file_creation': ('file_number', 'file_size', 'smallest_seqno', 'largest_seqno'),
    'table_file_deletion': ('file_number',),
}
MARKERS = ('GC 检查：', 'RR 开始：', 'RR 完成：', '统一决策：', '公共内容分配：',
           'V1后续迁移复制：', 'V1后续映射提交：', 'V1后续文件退役：', 'Zone 重置诊断：',
           'ZC 统计：', 'RR 统计：','受控GC检查点开始：','受控GC紧急检查：',
           '空间紧急分配等待：','空间紧急分配恢复：',
           '受控空间请求：','受控空间维护完成：','受控空间确认：')


def read_json(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def stamp(line):
    # 日志原始时钟直接转换；不用宿主本地时区二次偏移。
    value = datetime.strptime(line.split()[0], '%Y/%m/%d-%H:%M:%S.%f')
    delta = value - datetime(1970, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def difference(a, b):
    index = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    if index is None and len(a) != len(b):
        index = min(len(a), len(b))
    return dict(相同=index is None, 数量=[len(a), len(b)], 首差序号从零起=index)


def request_rows(path):
    with path.open('rb') as stream:
        if stream.read(8) != b'RRZCREQ1':
            raise ValueError('请求跟踪格式不符')
        next_index = [0, 0]
        sequence = 0
        previous_release = 0
        while data := stream.read(56):
            if len(data) != 56:
                raise ValueError('请求跟踪尾部不完整')
            seq, writer, index, key, start, api, release = struct.unpack('<7Q', data)
            if writer not in (0, 1) or seq != sequence or index != next_index[writer]:
                raise ValueError('请求编号、角色或角色内序号不连续')
            if not previous_release <= start <= api <= release:
                raise ValueError('前台请求重叠或时间倒退')
            next_index[writer] += 1
            sequence += 1
            previous_release = release
            yield (seq, writer, index, key, start, api, release)


def request_comparison(a, b):
    paths = [p/'请求提交.bin' for p in (a, b)]
    if not all(p.exists() for p in paths):
        return dict(已记录=False, 结论='只有批次及全局滚动摘要；不能定位单请求首差或精确维护请求位置')
    from itertools import zip_longest
    first = None
    counts = [0, 0]
    for index, pair in enumerate(zip_longest(*(request_rows(p) for p in paths))):
        for n, row in enumerate(pair):
            counts[n] += row is not None
        if first is None and (None in pair or pair[0][:4] != pair[1][:4]):
            first = dict(序号=index, g1=pair[0], g2=pair[1])
    return dict(已记录=True, 完整成功请求数=counts, 逻辑提交相同=first is None, 首差=first,
                边界='记录成功调用；失败调用会使业务不通过；Key为GenerateKeyFromInt输入，未记录Value字节')


def initial(a, b):
    pairs = {}
    for name in ('zones.before.json', 'space.before.json', '逻辑输入回放.json'):
        values = [read_json(p/name) for p in (a, b)]
        pairs[name] = dict(相同=values[0] == values[1])
    for name in ('装载-0.sst.csv', '装载-0.extent.csv', '布局-0.sst.csv', '布局-0.extent.csv'):
        with (a/name).open() as f:
            x = list(csv.DictReader(f))
        with (b/name).open() as f:
            y = list(csv.DictReader(f))
        # 文件内容包含已保存字节数和键范围；重开元数据分开比较，不冒称全字节相同。
        pairs[name] = difference(x, y)
    blocks = [sorted(read_json(p/'blocks.before.json'), key=lambda r:(r['Zone'], r['块'])) for p in (a, b)]
    for title, keys in {
        '全部物理块身份': ('Zone', '块', 'Channel', 'LUN', 'Plane', '物理块'),
        '全部物理块RC': ('Zone', '块', 'RC'),
        '全部物理块近期增量及统计标志': ('Zone', '块', '十秒增量', '三十秒增量', '标志'),
        '设备会话及Reset代际原值': ('Zone', '块', '设备会话', '代际'),
    }.items():
        pairs[title] = difference([[r[k] for k in keys] for r in blocks[0]], [[r[k] for k in keys] for r in blocks[1]])
    pairs['起始阈值块数'] = [sum(r['RC'] >= 1024 for r in rows) for rows in blocks]
    pairs['生命周期计数器'] = [read_json(p/'strict.before.json') for p in (a, b)]
    pairs['完整检查点'] = '不是完整内存检查点：同一VM内Reset并回放逻辑输入，重新打开ZenFS/RocksDB，真实读取重建RC；累计设备计数和Reset代际不清零'
    pairs['未覆盖'] = ['C4标志是近期窗口有效性标志，不是RR待处理标志',
        '未保存FTL队列、完整LPN到PPA映射及所有介质可用时间线',
        '未保存在线主机RR队列快照；新进程初始化及低RC仅提供间接证据',
        '快照是分层顺序采集，非跨层原子快照；起跑后重开会更新元数据']
    return pairs


def collect(root):
    rock = []
    for number, line in enumerate((root/'run.rocksdb.log').read_text().splitlines(), 1):
        if 'EVENT_LOG_v1 ' not in line:
            continue
        data = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
        kind = data.get('event')
        if kind in ROCK_FIELDS:
            rock.append(dict(层='RocksDB', 事件=kind, 日志行=number, 时钟纳秒=data['time_micros']*1000,
                内容={k:data.get(k) for k in ROCK_FIELDS[kind]}, 任务=data.get('job'), 原文=line))
    zen = []
    for number, line in enumerate((root/'run.zenfs.log').read_text().splitlines(), 1):
        marker = next((m for m in MARKERS if m in line), None)
        if marker is None:
            continue
        payload = line.split(marker, 1)[1]
        # 原始代际保留在原文；比较时只移除跨轮累计编号和观测编号，不改Zone及业务数据。
        normalized = re.sub(r'主机代际=\d+/\d+', '主机代际=<另列保留>', payload)
        normalized = re.sub(r'观察序号=\d+', '观察序号=<另列保留>', normalized)
        zen.append(dict(层='ZenFS', 事件=marker[:-1], 日志行=number, 时钟纳秒=stamp(line),
                        线程=line.split()[1], 内容=normalized, 原文=line))
    offsets = []
    text = (root/'run.zenfs.log').read_text()
    for line in text.splitlines():
        m = re.search(r'服务时点纳秒=(\d+)', line)
        if m:
            offsets.append(stamp(line)-int(m[1]))
    offset = sorted(offsets)[len(offsets)//2] if offsets else None
    for event in rock + zen:
        event['单调纳秒估计'] = event['时钟纳秒']-offset if offset is not None else None
    merged = sorted(rock+zen, key=lambda e:(e['时钟纳秒'], e['层'], e['日志行']))
    for seq, event in enumerate(merged):
        event['派生事件序号'] = seq
    return rock, zen, merged


def associate_requests(events, path):
    """流式关联逐请求时点；跨日志时钟转换仍为估计，不能据此声称原子因果关系。"""
    if not path.is_file():
        return
    records=iter(request_rows(path));current=next(records,None);last=None
    for event in events:
        moment=event['单调纳秒估计']
        if moment is None:continue
        while current is not None and current[6]<moment:
            last=current;current=next(records,None)
        row=current if current is not None else last
        if row is None:continue
        seq,writer,index,key,start,api,release=row
        position=('请求提交前' if moment<start else 'API调用中' if moment<=api else
                  'API完成后等待放行' if moment<=release else '末请求放行后')
        event['请求时点关联估计']=dict(全局序号=seq,操作='WRITE' if writer else 'READ',
            角色内序号=index,Key=key,位置=position,
            限制='日志墙钟由RR服务时点换算，边界附近只作定位；非在线原子因果标记')


def compare_stream(a, b):
    result = difference([e['内容'] for e in a], [e['内容'] for e in b])
    index = result['首差序号从零起']
    if index is not None:
        result['上下文'] = {tag:rows[max(0,index-2):index+3] for tag, rows in [('g1',a), ('g2',b)]}
    return result


def accounting(root,log_name='run.zenfs.log'):
    text = (root/log_name).read_text()
    copies = re.findall(r'V1后续迁移复制：[^\n]*有效字节=(\d+)，复制成功=1', text)
    commits = re.findall(r'V1后续映射提交：[^\n]*成功=(\d+)', text)
    stats = maintenance(text)
    stats.update(成功复制事件=len(copies),复制日志字节和=sum(map(int,copies)),
        映射提交成功数=commits.count('1'),映射提交失败数=len(commits)-commits.count('1'),
        复制日志与维护汇总相同=sum(map(int,copies))==stats['总迁移字节'])
    for kind, pattern in [('ZC', r'ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone，累计迁移 (\d+) 字节'),
                          ('RR', r'RR 统计：尝试 (\d+) 次，本次迁移 (\d+) 字节，本次重置 (\d+) 个 Zone，累计迁移 (\d+) 字节')]:
        total = 0
        consistent = True
        for seq, size, resets, cumulative in re.findall(pattern, text):
            total += int(size)
            consistent &= total == int(cumulative)
        stats[kind+'累计账目闭合'] = consistent
    return stats


def transactions(root):
    """用原日志线程和维护边界关联复制/提交/Reset，绝不把阈值块数当物理块ID。"""
    pending = {}
    completed = []
    counts = {'RR':0,'ZC':0}
    for number,line in enumerate((root/'run.zenfs.log').read_text().splitlines(),1):
        tid = line.split()[1] if len(line.split())>=2 else None
        start = re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节',line)
        decision = fields(line,'统一决策：') if '统一决策：' in line else None
        kind = 'RR' if start else 'ZC' if decision and decision['实际存在']=='1' else None
        if kind:
            if tid in pending:
                raise ValueError('同线程维护嵌套，不能安全从旧日志关联')
            counts[kind]+=1
            pending[tid]=dict(派生维护编号=f'{kind}:{counts[kind]}',类型=kind,开始日志行=number,
                开始原文=line,Zone字节地址=int(start[1] if start else decision['实际Zone']),
                阈值块数=int(start[2]) if start else None,触发物理块ID=None,
                开始有效字节=int(start[3]) if start else None,复制=[],映射提交=[],Reset=[])
        row = pending.get(tid)
        if row is None:
            continue
        for marker,key in [('V1后续迁移复制：','复制'),('V1后续映射提交：','映射提交'),('Zone 重置诊断：','Reset')]:
            if marker in line:row[key].append(dict(日志行=number,字段=fields(line,marker)))
        end = ('RR 统计：' in line and row['类型']=='RR') or ('ZC 统计：' in line and row['类型']=='ZC')
        if end:
            size=int(re.search(r'(?:本轮|本次)迁移 (\d+) 字节',line)[1])
            row.update(结束日志行=number,结束原文=line,统计迁移字节=size,
                成功复制字节=sum(int(c['字段']['有效字节']) for c in row['复制'] if c['字段']['复制成功']=='1'))
            row['源Zone成功Reset']=any(int(r['字段']['Zone'])==row['Zone字节地址'] and r['字段']['状态']=='成功' for r in row['Reset'])
            row['复制到提交再源Reset']=bool(row['复制']) and all(any(
                int(z['字段']['Zone'])==row['Zone字节地址'] and z['字段']['状态']=='成功' and
                any(c['日志行']<m['日志行']<z['日志行'] and m['字段']['文件编号']==c['字段']['文件编号'] and
                    m['字段']['成功']=='1' for m in row['映射提交']) for z in row['Reset']) for c in row['复制'])
            completed.append(pending.pop(tid))
    return dict(完成维护=completed,未闭合=list(pending.values()),
        边界='维护编号是事后关联；RR开始日志的达阈值物理块字段是数量，历史日志没有触发块ID')


def flush_waits(root):
    """关联同job的SST创建和Flush结束，保留时间差而不将其命名为锁等待。"""
    created = {}
    rows = []
    for line_number, line in enumerate((root/'run.rocksdb.log').read_text().splitlines(), 1):
        if 'EVENT_LOG_v1 ' in line:
            event = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
            if event['event']=='table_file_creation':
                created[event['job']] = (event['time_micros']*1000, event['file_number'], line_number)
        match = re.search(r'\[JOB (\d+)\] Flush lasted (\d+) microseconds, and (\d+) cpu microseconds',line)
        if match:
            job, wall, cpu = map(int,match.groups())
            creation = created.get(job)
            rows.append(dict(任务=job, 日志行=line_number, 耗时微秒=wall, CPU微秒=cpu,
                文件编号=creation[1] if creation else None,
                SST创建到Flush耗时日志微秒=(stamp(line)-creation[0])/1000 if creation else None,
                原文=line))
    return rows


def decisions(root):
    frames = {}
    for line in (root/'run.zenfs.log').read_text().splitlines():
        if '统一成本：' in line:
            row = fields(line, '统一成本：')
            frames.setdefault(int(row['轮次']), dict(成本=[]))['成本'].append(
                (int(row['Zone']), int(row['有效']), row['安全']))
        for marker in ('统一决策：','时序方案决策：'):
            if marker not in line:continue
            row = fields(line, marker)
            f = frames.setdefault(int(row['轮次']), dict(成本=[]))
            f.update(选中=int(row['实际Zone']) if row['实际存在']=='1' else None,日志行=line)
    result = []
    for seq, f in sorted(frames.items()):
        safe = sorted((size, zone) for zone, size, safe in f['成本'] if safe=='1')
        f.update(轮次=seq,Greedy排名=[z for _,z in safe],最少有效量选择通过=f.get('选中')==(safe[0][1] if safe else None))
        result.append(f)
    return result


def analyze(source, output):
    output.mkdir(parents=True, exist_ok=False)
    roots = [source/t for t in ('g1', 'g2')]
    inputs = [batches((p/'run.log').read_text()) for p in roots]
    checkpoints = [re.findall(r'全局回放检查点：批次=(\d+)，操作数=(\d+)，摘要=(\d+)', (p/'run.log').read_text()) for p in roots]
    requests = dict(批次逻辑摘要相同=inputs[0]==inputs[1],全局顺序摘要相同=(checkpoints[0]==checkpoints[1]) if all(len(x)==24 for x in checkpoints) else None,
        全局检查点数=[len(x) for x in checkpoints],逐请求=request_comparison(*roots),
        模式='closed-loop：前一个操作及受控等待结束后推进，无固定时间戳replay或QPS限速',
        请求数='各臂786432 Get、589824 Put；业务进程结束前含显式Flush/WaitForCompact，独立验证阶段不计入业务维护')
    save(output/'初始状态.json',initial(*roots));save(output/'请求一致性.json',requests)
    all_events = [collect(p) for p in roots]
    for root,events in zip(roots,all_events):
        associate_requests(events[2],root/'请求提交.bin')
    comparisons = {}
    for kind in ROCK_FIELDS:
        comparisons['RocksDB/'+kind] = compare_stream(*[[e for e in x[0] if e['事件']==kind] for x in all_events])
    for marker in MARKERS:
        comparisons['ZenFS/'+marker[:-1]] = compare_stream(*[[e for e in x[1] if e['事件']==marker[:-1]] for x in all_events])
    for tag, values in zip(('g1','g2'), all_events):
        with (output/(tag+'-跨层事件.jsonl')).open('w') as stream:
            for row in values[2]:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    save(output/'各类首差及上下文.json',comparisons)
    waits = [flush_waits(p) for p in roots]
    save(output/'Flush时序.json',dict(各臂=dict(zip(('g1','g2'),waits)),
        累计耗时秒=[sum(x['耗时微秒'] for x in rows)/1e6 for rows in waits],
        解释='SST创建后间隔不能仅凭日志认定锁或磁盘阻塞；需要在该区间追加阻塞栈和锁采证'))
    ranks = [decisions(p) for p in roots]
    same_state_different_decision = [dict(g1=a,g2=b) for a,b in zip(*ranks)
        if sorted(a['成本'])==sorted(b['成本']) and a.get('选中')!=b.get('选中')]
    save(output/'Greedy候选排名.json',dict(各臂=dict(zip(('g1','g2'),ranks)),
        同成本资格快照不同选择=same_state_different_decision,
        边界='快照包含有效量和安全资格；不声称包括所有并发内部状态。按轮次配对仅作诊断'))
    save(output/'原因分类.json',dict(
        输入不同=dict(证据='批次和全局摘要，逐请求信息见请求一致性.json',已观察到摘要差异=not requests['批次逻辑摘要相同'] or requests['全局顺序摘要相同'] is False),
        状态不同=dict(证据='初始状态.json和逐轮GC候选排名；首轮是否相同由实测字段列出，不预设不同',
            首轮GC成本资格相同=bool(ranks[0] and ranks[1] and sorted(ranks[0][0]['成本'])==sorted(ranks[1][0]['成本']))),
        事件交错不同=dict(证据='按每类逻辑序列核对内容，再按GC检查时点比较状态；不同墙钟本身不算决策差异',
            各层交错序列=compare_stream(*[[dict(e,内容=[e['层'],e['事件'],e['内容']]) for e in events[2]] for events in all_events])),
        同状态不同决策=dict(已记录成本资格相同但选择不同的轮数=len(same_state_different_decision),
            两轮均符合Greedy排名=all(f['最少有效量选择通过'] for frames in ranks for f in frames),
            限制='只能对已记录成本资格进行判断；不能据此排除所有潜在并发bug')))
    metrics = {}
    for tag, root in zip(('g1','g2'),roots):
        native = (root/'run.log').read_text()
        metrics[tag] = dict(维护=accounting(root),计时=timing(root),后台任务=background_overlap((root/'run.rocksdb.log').read_text()))
        wait = re.findall(r'受控后台排空：Put次数=(\d+)，累计纳秒=(\d+)',native)
        if wait:metrics[tag]['后台排空等待秒']=int(wait[0][1])/1e9
        save(output/(tag+'-维护关联.json'),transactions(root))
    save(output/'统计口径核验.json',metrics)
    hashes = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.iterdir()
        if p.is_file() and p.suffix in ('.json','.log','.csv','.ns','.bin')}
    hashes[str(Path(__file__).resolve())]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output/'分析源码.py').write_bytes(Path(__file__).read_bytes())
    save(output/'来源SHA256.json',hashes)
    save(output/'边界.json',dict(源目录=str(source),
        限制=['事件序号是事后按日志时间排序；同微秒和跨线程无严格因果保证',
        '旧日志缺少逐请求跟踪、阈值触发物理块ID及统一在线维护ID；不虚构补齐',
        'RC统计标志不代表RR等待标志；不能据此宣布无状态残留',
        '首次某类序列不同不等于已证明根因；先核对状态，再分析事件交错']))
    print(json.dumps(dict(结果目录=str(output),维护={k:v['维护'] for k,v in metrics.items()},
        同状态不同决策=len(same_state_different_decision)),ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    analyze(args.source,args.output)
