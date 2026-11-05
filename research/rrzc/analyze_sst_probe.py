"""真实YCSB短测验收与风险/退役交集；观察标签不冒充反事实RR节省。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_recent_eta import validate_rate
from analyze_sst_survival import parse_rocks, parse_zenfs, features, label, stamp, SECOND
from analyze_v1_shadow_stream import fields, require
from analyze_ycsb_retention import measurements
from rrzc_rr_health import inspect
from ycsb_throughput import throughput


def latency(text, operations):
    out = measurements(text, operations)
    row = [s for s in text.splitlines() if ' sec: ' in s and 'operations;' in s][-1]
    for op, body in re.findall(r'\[([^:]+):([^\]]+)\]', row):
        for key in ('99.9', '99.99'):
            values = re.findall(r'(?:^|\s)'+re.escape(key)+r'=([\d.]+)(?=\s|$)', body)
            require(len(values) == 1, '缺真实长尾分位：'+key)
            out[op]['p'+key+'微秒'] = float(values[0])
    return out


def opportunities(rock_text, zen_text):
    files, rock_end, _ = parse_rocks(rock_text)
    retirements, candidate_files, zen_end = parse_zenfs(zen_text)
    deleted = {fid for fid, r in files.items() if '删除可见微秒' in r}
    require(deleted == set(retirements), '删除与持久化退役不一一对应')
    for fid, r in retirements.items():
        file = files[fid]
        require(file['创建可见微秒'] <= r['退役微秒'] <= file['删除可见微秒'], '退役时间顺序错误')
        file.update(r)
    end = min(rock_end, zen_end)
    blocks, rates, membership = {}, {}, {}
    for line in zen_text.splitlines():
        if 'V1影子候选：' in line:
            r = fields(line, 'V1影子候选：')
            blocks[r['轮次'], r['Zone']] = dict(r, 时点=stamp(line))
        if '近期ETA对照：' in line:
            r = fields(line, '近期ETA对照：')
            require(r['新策略'] == '1', '近期ETA未实际执行')
            rates[r['轮次'], r['Zone']] = r
    for key,r in rates.items():
        validate_rate(r,blocks.get(key))
    for r in candidate_files:
        membership.setdefault((r['轮次'], r['Zone']), []).append(r)
    eligible = {k: r for k, r in blocks.items() if r.get('原候选资格') == '1'}
    details, matched = [], 0
    for key, b in eligible.items():
        when = b['时点']
        rate = rates.get(key)
        if not rate:
            continue
        matched += 1
        # 不把Zone最大RC差伪装为同一物理块速率；端点已由既有独立重算检查。
        eta = int(rate['新ETA'])
        if b.get('统计准入') != '1' or eta > 10_000_000_000:
            continue
        require(int(rate['阈值']) == 1024 and b['当前RC'] == rate['RC']
                and b['设备代际'] == rate['代际'] and b['块编号'] == rate['块'], '近期块与候选身份不符')
        records = []
        for member in membership.get(key, []):
            fid = member['SST编号']
            require(fid in files and files[fid]['创建成功'], '候选缺成功创建的SST')
            file = files[fid]
            require('ZenFS编号' not in file or file['ZenFS编号'] == member['文件编号'], '候选编号关联错误')
            concurrent = file.get('退役微秒', end+1) <= when
            r = dict(SST编号=fid, 文件编号=member['文件编号'],
                区内有效字节=int(member['区内有效字节']), 快照退役竞态=concurrent,
                ETA前退役=None if concurrent else label(file, when, (eta+999)//1000, end),
                十秒内退役=None if concurrent else label(file, when, 10*SECOND, end))
            if file['创建可见微秒'] < when:
                r.update(features(file, when))
            records.append(r)
        details.append(dict(轮次=key[0], Zone=key[1], 代际=b['设备代际'], 块编号=b['块编号'],
            ETA秒=eta/1e9, 当前有效字节=int(b['有效字节']),
            SST覆盖字节=sum(r['区内有效字节'] for r in records),
            ETA前已退役字节=sum(r['区内有效字节'] for r in records if r['ETA前退役'] == 1),
            十秒内已退役字节=sum(r['区内有效字节'] for r in records if r['十秒内退役'] == 1),
            文件=records))
    identities = {(r['Zone'], r['代际']) for r in details}
    return dict(原合格候选数=len(eligible), 同块近期可核验候选数=matched,
        近期风险候选数=len(details), 近期风险不同Zone代际数=len(identities),
        同时存在ETA前退役文件候选数=sum(r['ETA前已退役字节'] > 0 for r in details),
        同时存在十秒退役文件候选数=sum(r['十秒内已退役字节'] > 0 for r in details),
        近期风险明细=details, 末尾未退役SST数=len(files)-len(retirements),
        机会边界='仅报告原运行真实事件；候选被迁移、RR或Reset后不能验证未迁移块越阈反事实。'
        '相同文件键区间未采集，逻辑读写重叠不等于这些文件或Zone必然共置；未观察机会也不是永远无效。')


def experiment_size(mode, expected_records=327680):
    """规模必须显式登记；旧调用仍严格验收旧默认，不从结果反推预期规模。"""
    require(mode in ('smoke', 'disjoint', 'overlap'), '未知先导模式')
    require(expected_records in (327680, 491520), '未登记的装载规模')
    return (8192,8192,64,64,2048) if mode == 'smoke' else (
        expected_records,400000,4096,0 if mode == 'overlap' else 4096,65536)


def gc_coverage(text):
    """分开检查、资格、执行与应急回收；不把进入GC或零候选叫作ZC完成。"""
    checks, summaries, decisions, blocks, costs, rates = [], [], {}, {}, {}, {}
    for line in text.splitlines():
        if 'GC 检查：' in line:
            checks.append(fields(line, 'GC 检查：'))
        if 'GC 选区汇总：' in line:
            summaries.append(fields(line, 'GC 选区汇总：'))
        if '统一决策：' in line:
            row = fields(line, '统一决策：')
            require(row['轮次'] not in decisions, '统一决策轮次重复')
            decisions[row['轮次']] = row
        for marker, target in (('V1影子候选：', blocks), ('统一成本：', costs), ('近期ETA对照：', rates)):
            if marker in line:
                row = fields(line, marker)
                target[row['轮次'], row['Zone']] = row
    rounds = []
    for rid, row in decisions.items():
        eligible = {key for key, b in blocks.items() if key[0] == rid and b.get('原候选资格') == '1'}
        safe = {key for key, c in costs.items() if key[0] == rid and c.get('安全') == '1'}
        urgent = {key for key in safe if key in rates and blocks.get(key, {}).get('统计准入') == '1'
                  and int(rates[key]['新ETA']) <= 10_000_000_000}
        rounds.append(dict(轮次=rid, 原合格候选数=len(eligible), 安全候选数=len(safe),
            安全近期风险候选数=len(urgent), 实际选择存在=row['实际存在'] == '1', 实际Zone=row['实际Zone']))
    free = [int(r['空闲比例'].rstrip('%')) for r in checks]
    return dict(GC检查数=len(checks), 进入GC检查数=sum(r['进入选区'] == '1' for r in checks),
        最低空闲百分比=min(free) if free else None, 最高空闲百分比=max(free) if free else None,
        原无效比例门槛分布=dict(Counter(r['无效比例门槛'] for r in summaries)),
        正常模型决策轮数=len(rounds), 存在原合格候选轮数=sum(r['原合格候选数'] > 0 for r in rounds),
        多个原合格候选轮数=sum(r['原合格候选数'] >= 2 for r in rounds),
        多个安全候选轮数=sum(r['安全候选数'] >= 2 for r in rounds),
        多安全候选且含近期风险轮数=sum(r['安全候选数'] >= 2 and r['安全近期风险候选数'] > 0 for r in rounds),
        空间应急决策数=text.count('空间紧急回收决策：'),
        公共单区恢复选择数=len(re.findall(r'公共单区恢复决策：存在=1，', text)), 决策明细=rounds,
        口径='检查或选择存在不等于成功ZC；完成次数仍由ZC统计及Reset/复制账目核验。应急回收不冒充正常RR感知选区。')


def analyze_arm(root, mode, *, expected_records=327680):
    root = Path(root)
    records, ops, hot, start, count = experiment_size(mode, expected_records)
    for name in ('wrapper.exit', 'business.exit', 'verify.exit', 'runner.exit'):
        require((root/name).read_text().strip() == '0', '未成功：'+name)
    prefix = Path('/home/femu/rrzc-sst-results')/mode
    for line in (root/'logs.sha256').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        relative = Path(name).relative_to(prefix)
        require(hashlib.sha256((root/relative).read_bytes()).hexdigest() == digest, '归档原始哈希错误')
    before, after = [(root/f'software.{side}.log').read_text() for side in ('before', 'after')]
    require(before == after and before and all(s.endswith(': OK') for s in before.splitlines()), '实际软件身份改变')
    properties = dict(s.split('=',1) for s in (root/'workload.properties').read_text().splitlines()
                      if '=' in s and not s.startswith('#'))
    for key, value in dict(recordcount=records, operationcount=ops,
        **{'rrzc.read_key_count':hot, 'rrzc.update_key_start':start, 'rrzc.update_key_count':count}).items():
        require(properties[key] == str(value), '派生配置不符：'+key)
    require(properties['rocksdb.disable_wal'] == 'true' and properties['rocksdb.no_block_cache'] == 'true'
            and properties['rocksdb.fs_uri'] == 'zenfs://dev:nvme0n1', '无WAL/缓存/真实设备配置不符')
    require(f'只读全键最新值哈希校验通过，键数={records}' in (root/'verify.log').read_text(), '最新值全键校验缺失')
    verify = (root/'zenfs.verify.log').read_text()
    require('RR 开始：' not in verify and 'GC 检查：' not in verify, '独立校验不应产生维护')
    sys.path.insert(0, str(Path(__file__).resolve().parent/'tests'))
    from sst_probe_trace import check
    trace = check(root/'audit.requests', records, hot, ops, start, count)
    ycsb = (root/'ycsb.log').read_text()
    require(ycsb.count('实验WAL配置：关闭=1') == 1, '绑定实际禁用WAL未确认')
    counts = dict(s.split('=',1) for s in (root/'audit.counts').read_text().splitlines())
    require(counts == {'装载':str(records), '读取':str(trace['读取数']), '更新':str(trace['更新数']), '键数':str(records)},
            '审计请求计数不符')
    zen = (root/'zenfs.run.log').read_text()
    require('统一选区配置：启用=1，' in zen and '公共内容隔离配置：启用=1，' in zen, '实际机制入口不符')
    levels = set(re.findall(r'GC 检查：.*启动水平=(\d+)%', zen))
    require(not levels or levels == {'20'}, 'GC启动条件改变')
    require(not any(s for s in zen.splitlines() if '[ERROR]' in s or '状态=失败' in s), '维护存在错误')
    rock = (root/'aux/rrzc-ycsb/LOG').read_text()
    require(not re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:', rock), 'RocksDB后台错误')
    zc = [tuple(map(int,m.groups())) for m in ZC_STATS.finditer(zen)]
    rr = [tuple(map(int,m.groups())) for line in zen.splitlines() if (m := RR_STATS.search(line))]
    require(all(r[2] == 1 for r in zc+rr), '维护未完成单区Reset')
    copied = [fields(s, 'V1后续迁移复制：') for s in zen.splitlines() if 'V1后续迁移复制：' in s]
    moved = sum(r[1] for r in zc+rr)
    require(sum(int(r['有效字节']) for r in copied if r['复制成功'] == '1') == moved
            and zen.count('RR 开始：') == len(rr) and zen.count('RR 完成：') == len(rr), '复制/RR账目不闭合')
    health = inspect(zen)
    require(health['验收通过'], 'RR健康验收失败：'+str(health['错误']))
    devices = [json.loads((root/f'device.{side}.json').read_text()) for side in ('initial','before','after')]
    require(all(d['每区块数'] == 128 and d['RR阈值'] == 1024 and len(d['Zone']) == 32 for d in devices), '设备几何不符')
    require(len({(d['会话高'],d['会话低']) for d in devices}) == 1, '设备会话改变')
    initial, before_dev, after_dev = devices
    require(all(z['代际'] == 0 and z['RC'] == 0 for z in initial['Zone']), '不是新NAND运行状态')
    first = {z['地址']:z['代际'] for z in before_dev['Zone']}
    last = {z['地址']:z['代际'] for z in after_dev['Zone']}
    require(first.keys() == last.keys() and all(last[z] >= first[z] for z in first), '设备代际集合改变或回退')
    resets = sum(last[z]-first[z] for z in first)
    logged = len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功，', zen))
    require(resets == logged and resets >= len(zc)+len(rr), '设备Reset代际与成功日志不一致')
    return dict(验收通过=True, 模式=mode, 真实请求=trace, RR健康=health,
        ZC次数=len(zc), RR次数=len(rr), 总迁移字节=moved,
        ZC迁移字节=sum(r[1] for r in zc), RR迁移字节=sum(r[1] for r in rr),
        总成功ZoneReset=resets, 推导物理Block擦除次数=resets*128,
        擦除口径='业务前后C2代际增量，核对成功Reset日志；含自然空区/元数据Reset，不含mkfs。乘128仅按本轮映射推导，不是独立擦除计数器。',
        API调用延迟=latency(ycsb, ops), 吞吐量=throughput(ycsb, ops, properties),
        GC覆盖=gc_coverage(zen),
        **opportunities(rock, zen),
        边界='4GiB功能先导、单轮串行，无WAL。API延迟非原始NVMe延迟；Update含Get再Put。'
        '吞吐含客户端开销和1000ops/s节流；非饱和性能。两臂改变逻辑交集而非策略，不作模型收益因果对照。')
