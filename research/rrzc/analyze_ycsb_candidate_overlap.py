"""离线定位已完成先导的候选交集断点；不改策略，不用Zone最大RC差值估计同块速率。"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from analyze_v1_shadow_stream import fields, require

MIB = 1 << 20
FULL = re.compile(r'ZC 候选：Zone (\d+)，无效比例 (\d+)%，有效数据 (\d+) 字节，'
                  r'RR读数阈值 (\d+)，最大读数 (\d+).*，选中 ([01])$')
RR = re.compile(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节')
GC = re.compile(r'GC 检查：.*空闲比例=(\d+)%，启动水平=(\d+)%，进入选区=([01])')


def stamp(line):
    return datetime.strptime(line[:26], '%Y/%m/%d-%H:%M:%S.%f')


def same_epoch(first, second):
    # 只接受同一日志内可信的三点主机代际和序号，不跨Reset拼接同地址观测。
    return all(row.get('重置窗稳定') == '1' for row in (first, second)) and all(
        len(set(first[key].split('/') + second[key].split('/'))) == 1
        for key in ('主机代际', '观察序号'))


def funnel(rows, budget, horizon):
    result = Counter(候选观测数=len(rows))
    growing = []
    for item in rows:
        c, b, g, s = (item[key] for key in ('空间', '同块', '增长', '短期'))
        valid, capacity = int(c['有效字节']), int(c['容量字节'])
        spatial = 0 < valid <= capacity and capacity - valid >= budget
        covered, target = c['覆盖资格'] == '1', c['空目标资格'] == '1'
        eligible = spatial and covered and target
        require(eligible == (b['原候选资格'] == '1'), '离线资格与记录不符')
        result['未满足回收预算'] += not spatial
        result['覆盖资格不通过'] += not covered
        result['目标资格不通过'] += not target
        result['全部观测近期无增长'] += g['增量可知'] == '1' and int(g['真实增量']) == 0
        if not eligible:
            continue
        result['合格候选观测数'] += 1
        result['合格且统计准入'] += b['统计准入'] == '1'
        known = s['短期预测可知'] == '1'
        result['合格且短期预测可知'] += known
        near = known and int(s['短期达阈值纳秒']) <= horizon
        result['合格且近期紧迫'] += near
        result['合格非Greedy且近期紧迫'] += near and c['Zone'] != item['GreedyZone']
        result['合格且近期无增长'] += g['增量可知'] == '1' and int(g['真实增量']) == 0
        if g['增量可知'] == '1' and int(g['真实增量']) > 0:
            growing.append(dict(轮次=item['轮次'], Zone=int(c['Zone']),
                当前RC=int(b['当前RC']), 有效MiB=valid/MIB,
                同块真实增量=int(g['真实增量']), 间隔纳秒=int(g['间隔纳秒']),
                ETA秒=int(s['短期达阈值纳秒'])/1e9 if known else None,
                是否Greedy=c['Zone'] == item['GreedyZone'], 行号=item['行号']))
    return dict(result), growing


def analyze(lines, budget=192*MIB, horizon=10_000_000_000):
    require(budget > 0 and horizon > 0, '预算和界限必须为正')
    pending = {key: {} for key in ('空间', '同块', '增长', '短期')}
    markers = {'空间': '简单双因素候选：', '同块': 'V1影子候选：',
               '增长': 'V1增长候选：', '短期': '独立约束候选：'}
    observations, full, resets = {}, {}, {}
    gc_checks, rounds, candidates, rr_events, rejected_scans = [], [], [], [], []
    first_decision = None
    for number, line in enumerate(lines, 1):
        if 'Zone 重置诊断：' in line:
            row = fields(line, 'Zone 重置诊断：')
            if row['状态'] == '成功':
                resets[int(row['Zone'])] = number
        if 'Zone 维护观测：' in line:
            row = fields(line, 'Zone 维护观测：')
            observations[(row['阶段'], int(row['Zone']))] = row
            if row['阶段'] == 'RR扫描' and row['SST覆盖资格'] == '0':
                rejected_scans.append(dict(行号=number, **row))
        if match := GC.search(line):
            free, start, entered = map(int, match.groups())
            gc_checks.append(dict(行号=number, 时间=line[:26], 空闲比例=free,
                                  启动水平=start, 进入选区=entered))
        if match := FULL.search(line):
            zone, garbage, valid, threshold, rc, selected = map(int, match.groups())
            require(gc_checks and gc_checks[-1]['进入选区'], '满区观测缺GC上下文')
            require(('ZC候选', zone) in observations, '满区观测缺代际记录')
            full[zone] = dict(行号=number, 时间=line[:26], Zone=zone, 无效比例=garbage,
                有效MiB=valid/MIB, 当前最大RC=rc, RR阈值=threshold, 原比例门槛资格=selected,
                回收预算资格=(256*MIB-valid >= budget), GC=gc_checks[-1],
                代际观测=observations[('ZC候选', zone)])
        for key, marker in markers.items():
            if marker in line:
                row = fields(line, marker)
                require(row['Zone'] not in pending[key], '同轮候选重复')
                pending[key][row['Zone']] = dict(row, 行号=number)
        if '独立约束决策：' in line:
            decision = fields(line, '独立约束决策：')
            require(all(part.keys() == pending['空间'].keys() for part in pending.values()), '候选记录不完整')
            require(int(decision['界限纳秒']) == horizon, '实际紧迫界限不同')
            for zone in pending['空间']:
                require(all(pending[key][zone]['轮次'] == decision['轮次']
                            for key in ('同块', '增长', '短期')), '候选轮次混接')
                candidates.append(dict(轮次=decision['轮次'], GreedyZone=decision['GreedyZone'],
                    行号=number, **{key: pending[key][zone] for key in pending}))
            rounds.append(dict(行号=number, 时间=line[:26], **decision))
            if first_decision is None:
                first_decision = number
            pending = {key: {} for key in pending}
        if match := RR.search(line):
            zone, blocks, valid = map(int, match.groups())
            prior = full.get(zone)
            current = observations.get(('RR开始', zone), {})
            compatible = (prior is not None and resets.get(zone, 0) < prior['行号']
                          and same_epoch(prior['代际观测'], current))
            row = dict(序号=len(rr_events)+1, 行号=number, 时间=line[:26], Zone=zone,
                有效MiB=valid/MIB, 达阈值块=blocks,
                首次进入GC之前=not any(g['进入选区'] for g in gc_checks),
                首次正常选区决策之前=first_decision is None,
                最近满区GC同代际=compatible)
            if compatible:
                row['此前满区观测'] = prior
                row['观测到RR间隔秒'] = (stamp(line)-stamp(prior['时间'])).total_seconds()
            else:
                row['此前满区观测'] = None
            rr_events.append(row)
    require(not any(pending.values()), '日志结尾存在未闭合候选')
    require(rounds and rr_events, '没有正常选区或RR，不能用本分析冒充交集诊断')
    counts, growing = funnel(candidates, budget, horizon)
    eligible_rc = [int(c['同块']['当前RC']) for c in candidates if c['同块']['原候选资格'] == '1']
    return dict(GC检查次数=len(gc_checks), GC进入选区次数=sum(g['进入选区'] for g in gc_checks),
        正常决策轮数=len(rounds), RR次数=len(rr_events),
        首次进入GC之前RR次数=sum(r['首次进入GC之前'] for r in rr_events),
        首次正常选区之前RR次数=sum(r['首次正常选区决策之前'] for r in rr_events),
        候选筛选=counts, 合格候选最高RC=max(eligible_rc) if eligible_rc else None,
        合格正增长候选=growing, 决策原因=dict(Counter(r['原因'] for r in rounds)),
        后续RR源观测=rr_events, RR覆盖保护延后次数=len(rejected_scans),
        RR覆盖保护涉及Zone=sorted({int(r['Zone']) for r in rejected_scans}),
        RR覆盖保护首末证据=[rejected_scans[0], rejected_scans[-1]] if rejected_scans else [],
        候选明细=candidates,
        边界='候选按轮次计数而非不同Zone数；满区最大RC仅描述当时状态，不作同块增量；'
             '后续RR关联仅说明同代际观测顺序，不证明提前ZC的净收益；不跨Reset推断。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), '输出已存在，拒绝覆盖')
    raw = args.log.read_bytes()
    result = analyze(raw.decode().splitlines())
    result['原始日志'] = str(args.log.resolve())
    result['原始日志SHA256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ('GC检查次数', 'GC进入选区次数', '正常决策轮数',
        'RR次数', '首次进入GC之前RR次数', '首次正常选区之前RR次数', '候选筛选',
        '合格候选最高RC', '合格正增长候选', 'RR覆盖保护涉及Zone')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
