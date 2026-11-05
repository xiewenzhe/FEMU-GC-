"""只读核对直接模型的预测与后续事件；删失、同文件关联不冒充避免RR收益。"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from analyze_v1_shadow_stream import SECOND, fields, require
from direct_analysis import parse_direct
from rrzc_rr_health import inspect


def records(lines):
    """保留原始行号；只给已有日志加索引，不重建不存在的块读历史。"""
    blocks, costs, files = {}, {}, defaultdict(list)
    observations, changes, copies, retirements = defaultdict(list), defaultdict(list), [], defaultdict(list)
    active = {}
    for number, line in enumerate(lines, 1):
        prefix = line.split(maxsplit=2)
        thread = prefix[1] if len(prefix) == 3 else None
        if 'RR 开始：' in line:
            active[thread] = 'RR'
        elif 'GC 选区汇总：' in line:
            active[thread] = 'ZC'
        if 'V1影子候选：' in line:
            r = dict(fields(line, 'V1影子候选：'), 行号=number)
            blocks[r['轮次'], r['Zone']] = r
            if '当前RC' in r:
                observations[r['Zone']].append(r)
        if '统一成本：' in line:
            r = fields(line, '统一成本：')
            costs[r['轮次'], r['Zone']] = r
        if 'V1影子文件：' in line:
            r = fields(line, 'V1影子文件：')
            files[r['轮次'], r['Zone']].append(r)
        if 'V1后续迁移复制：' in line:
            r = dict(fields(line, 'V1后续迁移复制：'), 行号=number,
                     维护类型=active.get(thread, '未知'))
            copies.append(r)
            # 成败都可能意味着维护活动，预测验证不跨该观察拼接。
            for zone in {r['源Zone'], r['目标Zone']}:
                changes[zone].append((number, '发生迁移活动，预测验证删失'))
        if match := re.search(r'Zone 重置诊断：Zone=(\d+)，状态=(成功|失败)，', line):
            changes[match[1]].append((number, '发生Reset或Reset失败，预测验证删失'))
        if match := re.search(r'RR 开始：Zone (\d+)，', line):
            changes[match[1]].append((number, 'RR已开始，未观察到同块越阈时刻，删失'))
        if 'V1后续文件退役：' in line:
            r = fields(line, 'V1后续文件退役：')
            retirements[r['文件编号']].append(number)
        if any(marker in line for marker in ('RR 完成：', 'RR 未完成：', '主机端 RR 检查失败：', 'ZC 统计：')):
            active.pop(thread, None)
    return blocks, costs, files, observations, changes, copies, retirements


def validate_prediction(block, future, barriers, threshold=4096, horizon=10*SECOND):
    """只验证同设备会话、同代际、同物理块；阈值时刻只报告观测区间。"""
    identity = ('设备会话', '设备代际', '块编号', 'PPA', '代际起点纳秒')
    if block.get('统计准入') != '1' or '当前RC' not in block:
        return dict(结论='初始块统计未知，无法验证')
    start, previous = int(block['快照纳秒']), 0
    rc = int(block['当前RC'])
    if rc >= threshold:
        return dict(结论='初始已经达到阈值', 实际达阈值上界秒=0)
    for row in future:
        if row['行号'] <= block['行号']:
            continue
        barrier = next((reason for line, reason in barriers
                        if block['行号'] < line <= row['行号']), None)
        if barrier:
            return dict(结论=barrier)
        if row.get('统计准入') != '1':
            return dict(结论='后续块统计未知，预测验证删失')
        if any(row.get(key) != block.get(key) for key in identity):
            return dict(结论='最大块身份或Reset代际变化，不能拿最大RC差验证')
        elapsed = int(row['快照纳秒']) - start
        current = int(row['当前RC'])
        require(elapsed > previous and current >= rc, '同身份块时间或RC回退')
        if current >= threshold:
            result = ('窗口内已观察同块达阈值' if elapsed <= horizon else
                      '窗口后才达阈值' if previous >= horizon else
                      '达阈值区间跨预测窗口，无法判定是否及时')
            return dict(结论=result, 实际达阈值下界秒=previous/SECOND,
                        实际达阈值上界秒=elapsed/SECOND)
        if elapsed >= horizon:
            return dict(结论='同块跨预测窗口仍未达阈值',
                        后续观测秒=elapsed/SECOND, 后续RC=current)
        previous, rc = elapsed, current
    reason = next((why for line, why in barriers if line > block['行号']), None)
    return dict(结论=reason or '日志结束或不再入候选，无法验证')


def seconds_between(lines, first, last):
    def stamp(number):
        return datetime.strptime(lines[number-1].split()[0], '%Y/%m/%d-%H:%M:%S.%f')
    return (stamp(last)-stamp(first)).total_seconds()


def analyze(text):
    lines = text.splitlines()
    # 公共应急轮由既有工具独立核验，不混成正常模型输入；保留原始行号。
    replayed = lines
    for marker, module in (('空间紧急回收配置：启用=1，', 'replay_space_emergency'),
                           ('公共单区恢复配置：启用=1，', 'replay_single_recovery')):
        if marker in text:
            replayed, _ = __import__(module).replay(replayed)
    aligned, cursor = [], 0
    for line in lines:
        keep = cursor < len(replayed) and line == replayed[cursor]
        aligned.append(line if keep else '')
        cursor += int(keep)
    require(cursor == len(replayed), '重放结果不是原始日志子序列')
    decisions, _, maintenance = parse_direct(aligned)
    blocks, costs, files, observations, changes, copies, retirements = records(lines)
    selected = {d['轮次']: d for d in decisions if d['实际存在'] == '1'}
    by_file = defaultdict(list)
    for copy in copies:
        if copy['复制成功'] == '1':
            by_file[copy['文件编号']].append(copy)
    selected_copies, predictions, ledger = set(), [], []
    for key, cost in costs.items():
        rid, zone = key
        block = blocks[key]
        ids = {f['文件编号'] for f in files[key]}
        barriers = sorted(changes[zone] + [(n, '候选文件退役，预测验证删失')
                           for fid in ids for n in retirements[fid]])
        chosen = rid in selected and selected[rid]['实际Zone'] == zone
        if cost['风险可知'] == '1':
            outcome = validate_prediction(block, observations[zone], barriers)
            predictions.append(dict(轮次=rid, Zone=zone, 本轮选中=chosen,
                预测ETA秒=int(cost['ETA'])/SECOND, 近期预测=int(cost['ETA']) <= 10*SECOND,
                预计RR抵扣字节=int(cost['RR抵扣']), **outcome))
        if not chosen:
            continue
        decision = selected[rid]
        stop = next((n for n, _ in changes[zone] if n > decision['行号'] and
                     'Zone 重置诊断：' in lines[n-1]), len(lines)+1)
        own = [r for r in copies if decision['行号'] < r['行号'] < stop and
               r['源Zone'] == zone and r['复制成功'] == '1']
        later = {}
        for fid in ids:
            events = by_file[fid]
            for row in events[bisect_right([r['行号'] for r in events], stop):]:
                later[row['行号']] = row
        selected_copies.update(later)
        retired = [n for fid in ids for n in retirements[fid] if n > decision['行号']]
        ledger.append(dict(轮次=rid, Zone=zone, 与空间最少不同=decision['独立核验改选'],
            选择时有效字节=int(cost['有效']), 预计RR抵扣字节=int(cost['RR抵扣']),
            本轮源复制日志字节=sum(int(r['有效字节']) for r in own),
            候选文件覆盖完整=sum(int(r['区内有效字节']) for r in files[key]) == int(cost['有效']),
            候选文件数=len(ids), 后续同文件复制次数=len(later),
            后续同文件复制字节=sum(int(r['有效字节']) for r in later.values()),
            后续同文件RR复制字节=sum(int(r['有效字节']) for r in later.values() if r['维护类型'] == 'RR'),
            后续同文件ZC复制字节=sum(int(r['有效字节']) for r in later.values() if r['维护类型'] == 'ZC'),
            后续同文件复制行号=sorted(later),
            首个候选文件退役秒=seconds_between(lines, decision['行号'], min(retired)) if retired else None))
    completed = sum(int(r['本轮迁移字节']) for r in maintenance)
    urgent = [r for r in predictions if r['近期预测'] and not r['本轮选中']]
    return dict(分析完成=True, RR健康验收=inspect(text),
        决策轮数=len(decisions), 选择明细=ledger, 已完成ZC迁移字节=completed,
        预测明细=predictions, 未选中近期预测结论=dict(Counter(r['结论'] for r in urgent)),
        后续同文件复制去重字节=sum(int(r['有效字节']) for r in copies if r['行号'] in selected_copies),
        已证明避免RR次数=None,
        边界='复制成功不等于映射提交；同文件复制可能涉及其他片段，不等于相同字节重复搬运；各轮关联不能相加。'
             '被ZC、RR、文件退役、Reset或最大块身份变化干预的预测必须删失；'
             '未再入候选不等于未触发RR。观察到达阈值也不证明有效数据会持续存活。'
             'RR健康未通过的旧实验只可诊断，不能据此认定模型收益。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    raw = args.log.read_bytes()
    result = analyze(raw.decode())
    result.update(原始日志=str(args.log.resolve()), SHA256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
