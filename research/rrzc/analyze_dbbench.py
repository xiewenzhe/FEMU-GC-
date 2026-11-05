"""真实db_bench结果独立核验；原生操作直方图不是裸设备IO或精确API计时。"""
import json
from pathlib import Path
import re
from ycsb_af import digest, require
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_joint_opportunity import fields
from analyze_deadline import audit, audit_forecasts, choose
from analyze_space_plan import demand
from analyze_window_credit import audit_scores


def summary(text, name):
    matches = re.findall(r'^'+re.escape(name)+r'\s+:\s+([\d.]+) micros/op (\d+) ops/sec ([\d.]+) seconds (\d+) operations;([^\n]*)', text, re.M)
    require(len(matches) == 1, '缺少或重复原生基准结果：'+name)
    avg, speed, seconds, count, extra = matches[0]
    return dict(平均微秒=float(avg), 报告吞吐ops每秒=int(speed), 报告秒=float(seconds), 次数=int(count), 附加=extra)


def histograms(text):
    result = {}
    for op, count, mean, body in re.findall(r'Microseconds per (read|write):\s*\nCount: (\d+) Average: ([\d.]+)(.*?)(?=Microseconds per |\Z)', text, re.S):
        require(op.upper() not in result, '同类操作直方图重复')
        values = dict(re.findall(r'(P99(?:\.9|\.99)?): ([\d.]+)', body))
        require(set(values) == {'P99', 'P99.9', 'P99.99'}, '原生分位缺失')
        result[op.upper()] = dict(次数=int(count), 平均微秒=float(mean),
            **{k+'微秒':float(v) for k,v in values.items()})
    require(result, '缺少原生直方图')
    return result


def rr_changes(lines):
    groups = {}
    for line in lines:
        for tag in ('时序成本', '时序方案需求', '时序方案决策'):
            if tag+'：' in line:
                row = fields(line.split('，原因=', 1)[0], tag+'：')
                groups.setdefault(int(row['轮次']), {}).setdefault(tag, []).append(row)
    changes = []
    for n, frame in groups.items():
        require(len(frame.get('时序方案需求', [])) == len(frame.get('时序方案决策', [])) == 1, '同帧复算不闭合')
        d = frame['时序方案需求'][0]
        need = demand(*(int(d[k]) for k in ('空闲', '非空闲', '分配等待', '目标百分比')))
        rows = [dict(z=int(r['Zone']),v=int(r['有效']),cap=int(r['容量']),safe=int(r['安全']),p=0,credit=0,space=0)
                for r in frame.get('时序成本', [])]
        _, zero, _ = choose(rows, need)
        decision = frame['时序方案决策'][0]
        actual = int(decision['实际Zone']) if int(decision['实际存在']) else None
        if actual != zero:
            changes.append(dict(轮次=n, 实际Zone=actual, 同帧RR置零Zone=zero))
    return changes


def analyze(root, tag, case, mode, records, operations):
    root = Path(root)
    for name in ('load', 'run', 'verify', 'wrapper'):
        require((root/(name+'.exit')).read_text().strip() == '0', '业务未正常完成：'+name)
    for row in (root/'logs.sha256').read_text().splitlines():
        expected, source = row.split(maxsplit=1)
        relative = Path(source).relative_to('/home/femu/'+root.parent.name+'/'+tag)
        require('..' not in relative.parts and digest(root/relative) == expected, '原始证据哈希不符')
    identity = (root/'software.before.log').read_text()
    require(identity and identity == (root/'software.after.log').read_text() and
            all(s.endswith(': OK') for s in identity.splitlines()), '运行中软件身份改变')
    native = (root/'run.log').read_text()
    require(not re.search(r'get error:|put error:|Corruption:|unknown command line flag', native), 'db_bench实际错误')
    require(summary((root/'load.log').read_text(), 'fillseq')['次数'] == records, '初始装载数不符')
    require(summary((root/'verify.log').read_text(), 'readseq')['次数'] == records and
            'verifychecksum' in (root/'verify.log').read_text(), '全键及块CRC检查未完成')
    benchmark = 'readwhilewriting' if case == 'hot' else 'readrandomwriterandom'
    stats = summary(native, benchmark)
    latency = histograms(native)
    require('READ' in latency, '缺少读直方图')
    if case == 'hot':
        writes = 2048 if tag.startswith('smoke-') else 150000
        require(f'Exiting the writer after {writes} writes...' in native and
                latency['READ']['次数'] == operations and stats['次数'] == operations, '并发读写总量不符')
        reads = operations
    else:
        m = re.search(r'\( reads:(\d+) writes:(\d+) total:(\d+) found:(\d+)\)', native)
        require(m is not None, '缺少混合负载读写计数')
        reads, writes, total, found = map(int, m.groups())
        percent = 95 if case == 'mixed95' else 50
        expected_reads = operations//100*percent + min(operations%100, percent)
        require(total == stats['次数'] == operations and reads == found == expected_reads and
                writes == operations-reads and latency['READ']['次数'] == reads and
                latency['WRITE']['次数'] == writes, '混合比例或命中数不符')
    devices = [json.loads((root/('device.'+p+'.json')).read_text()) for p in ('initial', 'before', 'after')]
    require(all(len(d['Zone']) == 64 and d['每区块数'] == 128 and d['RR阈值'] == 1024 for d in devices), '设备几何或阈值不符')
    require(len({(d['会话高'],d['会话低']) for d in devices}) == 1 and
            all(z['代际'] == z['RC'] == 0 for z in devices[0]['Zone']), '不是全新同会话NAND')
    initial = json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(initial) == 64 and all(z['state'] == 'EMPTY' and z['wp'] == z['slba'] for z in initial), '非全空初态')
    rr = []; zc = []; resets = 0; positive = 0; changes = []; rounds = 0
    enabled = mode == 'model'
    for phase in ('load', 'run'):
        zen = (root/(phase+'.zenfs.log')).read_text(); lines = zen.splitlines()
        require('[ERROR]' not in zen and '状态=失败' not in zen, '维护错误')
        require(not re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:',
                (root/(phase+'.rocksdb.log')).read_text()), 'RocksDB后台实际错误')
        for marker in ('严格RR主机配置：启用=1，', '统一选区配置：启用=1，',
                       '时间一致配置：版本=1，启用=1，', '边界配置：版本=2，启用=0，'):
            require(zen.count(marker) == 1, '实际模型接线不符：'+marker)
        require(('窗口收益配置：' in zen) == enabled, 'RR预测开关不符')
        require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%', zen)) <= {'20'}, 'GC阈值改变')
        plans = audit(lines); forecasts = audit_forecasts(lines, time_consistent=True)
        if '统一成本：' in zen:
            audit_scores(lines, 'window' if enabled else 'greedy', persistent=enabled,
                expected_rr=enabled, candidate_pool='expanded', space_plan=True, deadline=True,
                joint_time=True, terminal=False, terminal_version=2)
        else:
            require(not plans, '有选区方案但缺少评分记录')
        rounds += len(plans); positive += sum(p['RR抵扣'] > 0 for p in forecasts)
        if enabled:
            changes += [dict(阶段=phase, **r) for r in rr_changes(lines)]
        a = [tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
        b = [tuple(map(int,m.groups())) for m in ZC_STATS.finditer(zen)]
        copies = [fields(s, 'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
        require(all(r[2] == 1 for r in a+b) and all(r['复制成功'] == '1' for r in copies) and
                sum(int(r['有效字节']) for r in copies) == sum(r[1] for r in a+b) and
                zen.count('RR 开始：') == zen.count('RR 完成：') == len(a), '维护复制账目不闭合')
        rr += a; zc += b
        resets += len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功，', zen))
    before, after = [{z['地址']:z['代际'] for z in d['Zone']} for d in devices[1:]]
    require(before.keys() == after.keys() and all(after[z] >= before[z] for z in before) and
            sum(after[z]-before[z] for z in before) == resets, 'Reset日志与设备代际不一致')
    strict = [json.loads((root/n).read_text()) for n in ('strict.business.json','strict.after-verify.json')]
    for s in strict:
        require(s['版本'] == s['启用'] == 1 and s['历史最高RC'] <= 1024 and
                s['实际NAND读取'] == s['计入RC读取']+s['自身刷新豁免读取'], '严格RR越阈值或计数不闭合')
    require(all(strict[1][k] >= strict[0][k] for k in strict[0]), '校验后计数回退')
    elapsed = (int((root/'run.end.ns').read_text())-int((root/'run.start.ns').read_text()))/1e9
    require(elapsed > 0, '进程墙钟无效')
    return dict(验收通过=True, 工作负载=case, 策略=mode, 实际读次数=reads, 实际写次数=writes,
        总业务操作=reads+writes, RR次数=len(rr), ZC次数=len(zc), 总迁移字节=sum(r[1] for r in rr+zc),
        RR迁移字节=sum(r[1] for r in rr), ZC迁移字节=sum(r[1] for r in zc), 总成功ZoneReset=resets,
        推导物理Block擦除次数=resets*128, 业务进程吞吐ops每秒=(reads+writes)/elapsed,
        业务进程秒=elapsed, 原生报告=stats, 操作延迟=latency, 严格RR业务账目=strict[0],
        正RR估计数=positive, RR独立改选次数=len(changes), 改选明细=changes, 实际选区轮数=rounds,
        公共参数SHA256=digest(root/'公共参数.txt'), 业务参数SHA256=digest(root/'业务参数.txt'),
        口径='业务维护含装载及关闭，不含独立全键迭代/CRC检查；原生直方图含基准循环开销；hot写线程不入合并直方图；并行非稳态，非裸IO')
