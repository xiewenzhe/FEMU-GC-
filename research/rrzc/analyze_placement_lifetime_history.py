"""按事件顺序回放可见Flush寿命历史；只作离线特征检验，不驱动放置。"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re

from analyze_placement_trace import rows, require

MODES = ('G2', 'G2_WSURVIVOR')
HORIZONS = (10, 30)  # 既有读窗口对应的诊断尺度，不是新增在线策略阈值。
SECOND = 10**9


def history_at(samples, now, age, horizon, exclude, event=None):
    """只使用now之前发生的出生和退役；尚未观察完整的样本保留为未知。"""
    counts = Counter()
    for fid, sample in samples.items():
        born, retired = sample['born'], sample.get('retired')
        if fid == exclude or born >= now or (event is not None and sample['born_event'] >= event):
            continue
        known_death = (retired if retired is not None and retired <= now and
                       (event is None or sample['retired_event'] < event) else None)
        observed = (known_death if known_death is not None else now)-born
        if observed < age or (known_death is not None and observed <= age):
            continue
        if known_death is not None and observed <= age+horizon:
            counts['已退役'] += 1
        elif observed >= age+horizon:
            counts['已存活'] += 1
        else:
            counts['未观察完整'] += 1
    total = sum(counts.values())
    low = counts['已退役']/total if total else None
    high = (counts['已退役']+counts['未观察完整'])/total if total else None
    # 仅用于描述性回放：不调优0.5，不把它引入运行准入，也不称为个体概率。
    verdict = ('未知' if not total else '历史多数及时退役' if low > 0.5 else
               '历史多数仍存活' if high < 0.5 else '历史不足以区分')
    return dict(历史样本数=total, 已知及时退役数=counts['已退役'],
                已知仍存活数=counts['已存活'], 随访未完整数=counts['未观察完整'],
                历史退役比例下界=low, 历史退役比例上界=high, 历史判断=verdict)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def collect(pair, mode):
    arm = pair/mode
    report = json.loads((arm/'业务核验.json').read_text())
    raw = arm/json.loads((arm/'状态.json').read_text())['标签']/'p1'
    tagged = {int(fid): dict(name=name, job=int(job)) for fid, name, job in
              re.findall(r'Flush来源W标记：文件编号=(\d+)，文件名=([^，]+)，任务=(\d+)',
                         (raw/'run.log').read_text())}
    native, jobs = {}, {}
    for number, line in enumerate((raw/'run.rocksdb.log').read_text().splitlines(), 1):
        if 'EVENT_LOG_v1 ' not in line:
            continue
        event = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
        if event.get('event') in ('flush_started', 'compaction_started'):
            jobs[event['job']] = (event['event'], number)
        if event.get('event') == 'table_file_creation':
            native[event['file_number']] = (event, number)
    cohort = Counter()
    for item in tagged.values():
        event, _ = native[int(Path(item['name']).stem)]
        source, _ = jobs[item['job']]
        require(source == 'flush_started', 'W标记没有对应真实Flush任务')
        cohort[(event['cf_name'], source)] += 1
    events = list(rows(raw/'组成.bin'))
    cutoff = max(r[0] for r in events if r[3] == 27 and r[7] == 2)
    end_ns = events[cutoff-1][1]
    # 完整退役表仅供标签核验；history_at显式截断，不得读取未来结局作为特征。
    samples, identity, primary = {}, {}, {}
    for r in events[:cutoff]:
        seq, ns, _, kind, _, owner, _ = r[:7]
        if kind == 2:
            identity[owner], primary[owner] = r[7], not r[11]
            if primary[owner] and r[10] and r[7] in tagged:
                samples.setdefault(r[7], dict(born=ns, born_event=seq))
        elif kind == 16 and primary.get(owner) and identity[owner] in samples:
            samples[identity[owner]].setdefault('retired', ns)
            samples[identity[owner]].setdefault('retired_event', seq)
    require(set(samples) == set(tagged), 'Flush来源日志与寿命对象不一一对应')
    targets = {t['事件']: t for t in report['组成']['目标'] if t['类型'] == '实际目标'}
    owners, maps, heat, pending_pool, migrated = {}, {}, {}, {}, set()
    clock, queries, uncertain = 0, [], 0
    for r in events[:cutoff]:
        seq, ns, _, kind, device, owner, event_clock = r[:7]
        a = r[7:];clock = max(clock, event_clock)
        if kind == 2:
            data = owners.setdefault(owner, dict(extents={}))
            data.update(file=a[0], profile=a[1], sst=a[2] == 1, w=bool(a[3]),
                        temporary=bool(a[4]), retired=bool(a[5]))
        elif kind == 3:
            container = maps[owner] if owner in maps else owners[owner]['extents']
            key = (device, a[0], a[1], a[2])
            require(key not in container, '重复extent增加')
            container[key] = a[3]
        elif kind == 4:
            owners[owner]['extents'].clear()
        elif kind == 5:
            maps[owner] = dict(owners[owner]['extents'])
        elif kind == 7:
            key = (device, a[0], a[1], a[2])
            require(maps[owner].pop(key) == a[3], 'extent移除不闭合')
        elif kind == 6:
            owners[owner]['extents'] = maps.pop(owner)
        elif kind == 8:
            heat[owner] = (bool(a[0]), a[1])
        elif kind == 14 and a[1] and owners[owner]['w'] and not owners[owner]['retired']:
            migrated.add(owner)
        elif kind == 16:
            owners[owner]['retired'] = True
        elif kind == 19:
            require(not owners[owner]['extents'], '销毁时extent未释放')
            del owners[owner]
        elif kind == 11:
            pending_pool[owner] = (a[0], a[1])
        elif kind == 12:
            target = targets[seq]
            pool = pending_pool.pop(owner, None) == (a[0], a[1])
            if not target['H状态已确认']:
                uncertain += 1;continue
            current = owners[owner]
            candidates = []
            if a[5] == 2 and current['file'] in samples and target['当前组成']['已知H字节']:
                candidates.append((owner, 0, '新Flush进入H区' if a[4] else 'W必要迁移进入H区'))
            if not a[4] and a[5] & 1 and target['当前组成']['W_only']:
                visible = Counter()
                for who, data in owners.items():
                    hot, expiry = heat.get(data['profile'], (False, 0))
                    if data['temporary'] or data['retired'] or not data['sst'] or not data['w'] or (hot and clock < expiry):
                        continue
                    length = sum(length for key, length in data['extents'].items()
                                 if key[:3] == (device, a[0], a[1]))
                    if length:
                        visible[who] += length
                require(sum(visible.values()) == target['当前组成']['W_only'], '目标W逐对象映射与已有组成不符')
                candidates.extend((who, length, 'H必要迁移进入W区') for who, length in visible.items()
                                  if owners[who]['file'] in samples)
            for who, length, route in candidates:
                fid = owners[who]['file'];sample = samples[fid]
                require(sample['born'] <= ns, '查询先于W可见出生')
                age = ns-sample['born']
                for horizon in HORIZONS:
                    window = horizon*SECOND
                    observed = history_at(samples, ns, age, window, fid, seq)
                    death = sample.get('retired')
                    label = (True if death is not None and ns < death <= ns+window else
                             False if end_ns >= ns+window else None)
                    queries.append(dict(模式=mode, 目标事件=seq, 申请文件=current['file'],
                        W文件=fid, W对象=who, 路径=route, 实际取得协同目标=pool,
                        Zone编号=a[0]//(1 << 27), 代际=a[1], 当前目标W引用字节=length,
                        查询单调纳秒=ns, W可见出生事件=sample['born_event'], W年龄秒=age/SECOND,
                        当时已成功迁移=who in migrated, 诊断窗口秒=horizon, **observed,
                        实际窗口内退役=label, 实际退役事件=sample.get('retired_event'),
                        未来结局仅用于评价=True))
    lifetimes = [dict(模式=mode, 文件编号=fid, 原生文件=tagged[fid]['name'],
        Flush任务=tagged[fid]['job'], 可见出生事件=s['born_event'], 可见出生纳秒=s['born'],
        逻辑退役事件=s.get('retired_event'), 逻辑寿命秒=(s['retired']-s['born'])/SECOND if 'retired' in s else None,
        截尾随访秒=(end_ns-s['born'])/SECOND) for fid, s in samples.items()]
    return queries, lifetimes, dict(真实Flush文件数=len(tagged),
        队列组成=[dict(CF=k[0], 来源=k[1], 数量=v) for k,v in cohort.items()],
        排除H状态未确认的目标数=uncertain,
        原始SHA256={str(raw/name):sha(raw/name) for name in
            ('组成.bin', 'run.log', 'run.rocksdb.log')})


def analyze(pair, output):
    require(not output.exists(), '不覆盖历史回放结果')
    queries, lifetimes, evidence = [], [], {}
    for mode in MODES:
        q, life, proof = collect(pair, mode)
        queries.extend(q);lifetimes.extend(life);evidence[mode] = proof
    groups = defaultdict(list)
    for q in queries:
        groups[(q['模式'], q['路径'], q['诊断窗口秒'])].append(q)
    summary = []
    for (mode, route, horizon), data in sorted(groups.items()):
        premature = [x for x in data if x['历史判断'] == '历史多数及时退役' and x['实际窗口内退役'] is False]
        summary.append(dict(模式=mode, 路径=route, 诊断窗口秒=horizon, 观察关联数=len(data),
            不同W文件数=len({x['W文件'] for x in data}),
            已观察及时退役关联数=sum(x['实际窗口内退役'] is True for x in data),
            末端截尾关联数=sum(x['实际窗口内退役'] is None for x in data),
            历史多数及时退役关联数=sum(x['历史判断'] == '历史多数及时退役' for x in data),
            历史乐观但实际仍存活关联数=len(premature),
            历史多数仍存活关联数=sum(x['历史判断'] == '历史多数仍存活' for x in data)))
    output.mkdir(parents=True)
    for name, data in [('历史前缀逐次回放.csv', queries), ('全部Flush寿命及截尾.csv', lifetimes),
                       ('回放分组汇总.csv', summary)]:
        with (output/name).open('x', encoding='utf-8-sig', newline='') as stream:
            writer=csv.DictWriter(stream, list(data[0]));writer.writeheader();writer.writerows(data)
    (output/'原始证据索引.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pair', type=Path);parser.add_argument('output', type=Path)
    args=parser.parse_args();print(json.dumps(analyze(args.pair,args.output), ensure_ascii=False, indent=2))
