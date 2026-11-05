"""只读核验选区时的有效量趋势；未来退役仅作标签，不生成反事实收益。"""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from analyze_all_blocks_dbbench import fields
from analyze_joint_value import audit, timestamp, ZONE


def retirement_label(files, retirements, when, horizon, end):
    """固定原候选文件集合；窗口不足整行删失，不能把未见退役当存活。"""
    if when + timedelta(seconds=horizon) > end:
        return None
    total = 0
    for f in files:
        death = retirements.get(f['编号'])
        if death is not None:
            assert death > when, '候选文件在观察之前已退役'
            if death <= when + timedelta(seconds=horizon):
                total += f['字节']
    return total


def extrapolate(feature, valid, horizon, faster=False):
    """仅诊断已有两段历史的线性外推，不把外推值当已验证收益。"""
    if feature is None or feature['留存可知'] != '1':
        return None
    pairs = [('短减少', '短间隔'), ('长减少', '长间隔')]
    assert all(int(feature[s]) > 0 for _, s in pairs)
    rates = [Fraction(int(feature[n]), int(feature[s])) for n, s in pairs]
    rate = max(rates) if faster else min(rates)
    return min(valid, int(rate * horizon * 10**9))


def rocks_events(lines):
    """以日志实际输出时间作为可见时间；不提前使用延迟输出事件。"""
    events = []
    for number, line in enumerate(lines, 1):
        if 'EVENT_LOG_v1 ' not in line:
            continue
        event = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
        events.append((timestamp(line), number, event))
    return events


def active_compactions(events, when):
    active = {}
    for visible, _, e in events:
        if visible > when:
            continue
        if e['event'] == 'compaction_started':
            active[int(e['job'])] = {int(fid) for key, values in e.items()
                if re.fullmatch(r'files_L\d+', key) for fid in values}
        elif e['event'] == 'compaction_finished':
            active.pop(int(e['job']), None)
    return set().union(*active.values()) if active else set()


def summarize(rows, horizon, uninterrupted=False):
    selected = [r for r in rows if r['标签'][str(horizon)] is not None and
        r['较慢外推'][str(horizon)] is not None and
        (not uninterrupted or r['后续首个位置变化秒'] is None or
         r['后续首个位置变化秒'] > horizon)]
    confusion = Counter()
    for r in selected:
        predicted = r['较慢外推'][str(horizon)] > 0
        actual = r['标签'][str(horizon)] > 0
        confusion[('预测有' if predicted else '预测无') + ('实际有' if actual else '实际无')] += 1
    result = dict(完整窗口且留存可知候选快照数=len(selected), 分类计数=dict(confusion),
        当前有效字节合计=sum(r['有效字节'] for r in selected),
        观察到退役字节合计=sum(r['标签'][str(horizon)] for r in selected), 诊断={})
    for key in ('不外推', '较慢外推', '较快外推'):
        predictions = [(0 if key == '不外推' else r[key][str(horizon)],
                        r['标签'][str(horizon)]) for r in selected]
        result['诊断'][key] = dict(
            预测字节合计=sum(p for p, a in predictions),
            绝对误差字节合计=sum(abs(p-a) for p, a in predictions),
            高估字节合计=sum(max(0, p-a) for p, a in predictions),
            低估字节合计=sum(max(0, a-p) for p, a in predictions))
    return result


def analyze(root):
    root = Path(root)
    ledger = audit(root)
    log = root / 'run.zenfs.log'
    lines = log.read_text().splitlines()
    rock = root / 'run.rocksdb.log'
    events = rocks_events(rock.read_text().splitlines())
    files, windows, raw, observations = defaultdict(list), {}, defaultdict(list), []
    retirements, changes = {}, defaultdict(list)
    for t in ledger['任务']:
        changes[t['Zone']].append((t['时刻'], t['类型']))
    for c in ledger['复制']:
        changes[c['目标Zone']].append((c['时刻'], '迁入'))
    for number, line in enumerate(lines, 1):
        when = timestamp(line)
        if 'Zone 重置诊断：' in line:
            r = fields(line, 'Zone 重置诊断：')
            changes[int(r['Zone']) // ZONE].append((when, 'Reset'))
        if 'V1后续文件退役：' in line:
            r = fields(line, 'V1后续文件退役：')
            fid = int(r['文件编号'])
            assert fid not in retirements
            retirements[fid] = when
        if 'V1影子文件：' in line:
            r = fields(line, 'V1影子文件：')
            match = re.fullmatch(r'/rrzc-dbbench/(\d+)\.sst', r['文件名'])
            files[int(r['轮次']), int(r['Zone'])].append(dict(
                编号=int(r['文件编号']), SST编号=int(match[1]) if match else None,
                字节=int(r['区内有效字节']), 行=number))
        if '窗口收益成本：' in line:
            r = fields(line, '窗口收益成本：')
            key = int(r['轮次']), int(r['Zone'])
            assert key not in windows
            windows[key] = dict(r, 行=number)
        if '留存原始采样：' in line:
            r = fields(line, '留存原始采样：')
            raw[int(r['Zone'])].append(dict(r, 行=number, 时刻=when))
        if '时序成本：' in line:
            r = fields(line, '时序成本：')
            if r['安全'] == '1':
                observations.append((number, when, r))
    end = timestamp(lines[-1])
    rows = []
    for number, when, c in observations:
        key = int(c['轮次']), int(c['Zone'])
        cohort = files[key]
        valid = int(c['有效'])
        assert sum(f['字节'] for f in cohort) == valid, '候选文件字节覆盖不完整'
        assert len({f['编号'] for f in cohort}) == len(cohort), '同候选文件重复'
        assert all(f['行'] < number for f in cohort)
        w = windows.get(key)
        assert w is not None and w['行'] < number
        for h in (10, 30):
            if w['留存可知'] == '1':
                assert int(w['留存有效']) == valid
        later = sorted((t, kind) for t, kind in changes[key[1]//ZONE] if t > when)
        active = active_compactions(events, when)
        row = dict(轮次=key[0], Zone=key[1]//ZONE, 观察行=number, 时刻=when,
            有效字节=valid, 留存特征=w, 文件=cohort,
            已进入Compaction字节=sum(f['字节'] for f in cohort if f['SST编号'] in active),
            已进入Compaction的SST=[f['SST编号'] for f in cohort if f['SST编号'] in active],
            后续首个位置变化秒=(later[0][0]-when).total_seconds() if later else None,
            后续首个位置变化类型=later[0][1] if later else None,
            标签={}, 进行中Compaction文件标签={}, 较慢外推={}, 较快外推={})
        for h in (10, 30):
            row['标签'][str(h)] = retirement_label(cohort, retirements, when, h, end)
            row['进行中Compaction文件标签'][str(h)] = retirement_label(
                [f for f in cohort if f['SST编号'] in active], retirements, when, h, end)
            row['较慢外推'][str(h)] = extrapolate(w, valid, h)
            row['较快外推'][str(h)] = extrapolate(w, valid, h, True)
        if key[0] == 6 and key[1]//ZONE == 60:
            # 原始样本只展示选区之前的同设备/主机代际，不能拼接Reset前历史。
            current = next(p for p in raw[key[1]] if p['阶段'] == '选区' and p['轮次'] == '6')
            epoch = tuple(current[k] for k in ('会话', '设备代际', '主机代际', '主机序号'))
            row['选区前满区历史'] = [p for p in raw[key[1]] if p['行'] <= current['行'] and
                p['可知满区'] == '1' and
                tuple(p[k] for k in ('会话', '设备代际', '主机代际', '主机序号')) == epoch and
                0 <= int(current['时点纳秒'])-int(p['时点纳秒']) <= 10*10**9]
        rows.append(row)
    active_rows = [r for r in rows if r['标签']['10'] is not None and r['已进入Compaction字节'] > 0]
    return dict(来源=str(root), 原始日志SHA256=ledger['SHA256'],
        RocksDB日志SHA256=hashlib.sha256(rock.read_bytes()).hexdigest(),
        安全候选快照数=len(rows),
        完整十秒窗口数=sum(r['标签']['10'] is not None for r in rows),
        完整三十秒窗口数=sum(r['标签']['30'] is not None for r in rows),
        已进入Compaction快照数=sum(r['已进入Compaction字节'] > 0 for r in rows),
        进行中Compaction十秒核对=dict(完整窗口快照数=len(active_rows),
            其中全部相关文件已退役快照数=sum(r['已进入Compaction字节'] ==
                r['进行中Compaction文件标签']['10'] for r in active_rows),
            相关字节合计=sum(r['已进入Compaction字节'] for r in active_rows),
            已退役字节合计=sum(r['进行中Compaction文件标签']['10'] for r in active_rows)),
        十秒=summarize(rows, 10), 三十秒=summarize(rows, 30),
        十秒未观察到位置干预=summarize(rows, 10, True),
        三十秒未观察到位置干预=summarize(rows, 30, True), 候选=rows,
        边界=[
            '特征只用本轮选区之前日志，未来文件退役只作固定集合标签。',
            '标签是实际运行中文件最后链接退役，不是未清理的反事实；空间位置变化单独标注。',
            '同一Zone或文件可在多轮重复出现，汇总字节按候选快照计，不能当唯一可节省字节。',
            '使用客机日志输出时间；跨层同微秒不声称原子因果全序。',
            '10/30秒只是诊断窗口，未经过训练或调参；当前短受控负载不足以证明泛化。'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root), ensure_ascii=False, indent=2, default=str))
