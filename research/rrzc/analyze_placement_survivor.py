"""W历史准入配对审计：核对每次拒绝的先前提交证据与实际去向。"""
import csv
import json
import re
import struct
from collections import defaultdict, deque

import analyze_placement_wmig as shared
import analyze_phase_time
from analyze_placement_business import analyze as business, compare
from analyze_placement_trace import rows
from ycsb_af import require

MODE = 'G2_WSURVIVOR'


def audit_history(root, report, mode):
    evaluations = defaultdict(deque)
    native_gates = defaultdict(deque)
    for lineno, line in enumerate((root / 'run.zenfs.log').read_text().splitlines(), 1):
        found = re.search(r'W历史准入评估：对象=(\d+)，历史状态=(\d+)，所需字节=(\d+)', line)
        if found:
            owner, state, size = map(int, found.groups())
            evaluations[owner].append((state, size, lineno))
        found = re.search(r'W历史准入回退：模式=G2_WSURVIVOR，对象=(\d+)，类别=(\d+)，需要字节=(\d+)', line)
        if found:
            owner, tag, size = map(int, found.groups())
            require(tag == 2, '历史准入误拒绝H或HW')
            native_gates[owner].append((size, lineno))
    attempts, current = [], {}
    owners, committed, transaction_copies, retired = {}, {}, defaultdict(list), set()
    for row in rows(root / '组成.bin'):
        seq, _, _, kind, _, owner, _ = row[:7]
        args = row[7:]
        if kind == 2:
            owners[owner] = dict(file=args[0], w=bool(args[3]))
        elif kind == 16:
            retired.add(owner)
        elif kind == 9:
            if args[0] != 2 or args[1]:
                current.pop(owner, None);continue
            previous = committed.get(owner)
            state = line = None
            if mode == MODE:
                require(bool(evaluations[owner]), '缺少对应W准入原生日志')
                state, size, line = evaluations[owner].popleft()
                require(size == args[2] and (state == 2) == bool(previous),
                        '准入历史与本会话先前成功提交证据不符')
            gated = mode == MODE and state == 2
            gate_line = None
            if gated:
                require(bool(native_gates[owner]), '有历史却缺少应执行的准入拒绝')
                size, gate_line = native_gates[owner].popleft()
                require(size == args[2], '拒绝所需容量与尝试不符')
            record = dict(尝试事件=seq, 对象=owner, 文件编号=owners[owner]['file'],
                本会话先前成功提交事件=previous, 原生历史状态=state, 评估日志行=line,
                拒绝日志行=gate_line, 拒绝协同=gated, 实际入池=False, 实际目标=None,
                目标代际=None, 复制事件=None, 实际复制字节=0, 映射结局='未找到复制')
            attempts.append(record);current[owner] = record
        elif kind == 11 and owner in current:
            current[owner]['实际入池'] = True
            require(not current[owner]['拒绝协同'], '已拒绝的W仍取得协同目标')
        elif kind == 12 and owner in current:
            current[owner].update(实际目标=args[0], 目标代际=args[1])
        elif kind == 13:
            transaction_copies[(owner, args[0])].append(seq)
            if owner in current:
                record = current.pop(owner)
                require(record['实际目标'] == args[3] and record['目标代际'] == args[6], 'W实际复制与取得目标不符')
                record.update(复制事件=seq, 实际复制字节=args[5])
        elif kind == 14 and args[1] == 1:
            if transaction_copies.get((owner, args[0])) and owners[owner]['w'] and owner not in retired:
                committed[owner] = seq
    require(not any(evaluations.values()) and not any(native_gates.values()), '原生W历史日志未全部匹配')
    copies = {c['事件']: c for c in report['组成']['复制明细']}
    for attempt in attempts:
        if attempt['复制事件'] is not None:
            copy = copies[attempt['复制事件']]
            require(copy['有效复制字节'] == attempt['实际复制字节'], 'W有效复制量不符')
            attempt['映射结局'] = copy['映射结局']
    return dict(逐次=attempts, 拒绝次数=sum(a['拒绝协同'] for a in attempts),
        有成功历史的W尝试=sum(a['本会话先前成功提交事件'] is not None for a in attempts),
        历史核验通过=True, 边界='仅本会话成功片段迁移；恢复未知不冒充新文件。拒绝字节不等于节省字节。')


def analyze(root, mode):
    require(mode in ('G2', MODE), '非本次配对模式')
    analyze_phase_time.KINDS[14] = 'aux目录同步实际调用'
    report = business(root, mode)
    log = (root / 'run.zenfs.log').read_text()
    require('放置消融配置：模式=' + mode + '，' in log, '实际放置模式未精确匹配')
    require('W迁移准入回退：' not in log and 'H回退目标排序：' not in log, '叠加了旧候选策略')
    history = audit_history(root, report, mode)
    report['W历史准入'] = history
    report['W迁移准入'] = dict(拒绝次数=history['拒绝次数'], 事件=history['逐次'])
    data = (root / '等待区间.bin').read_bytes()
    aux = [r for r in struct.iter_unpack('<6Q', data[16:]) if r[0] == 14]
    require(len(aux) == 720, '缺少720次共同aux调用记录')
    groups = {}
    for line in (root / 'run.log').read_text().splitlines():
        found = re.search(r'Flush分段：任务=(\d+)，文件=(\d+)，阶段=(.*?)，纳秒=(\d+)', line)
        if found:
            groups.setdefault(int(found[1]), {})[found[3]] = int(found[4])
    enveloped = 0
    for _, left, right, _, job, _ in aux:
        marks = groups[job]
        require(marks['目录同步前'] <= left <= right <= marks['目录同步后'], 'aux原生标记与内存计时不符')
        enveloped += marks['目录同步后'] - marks['目录同步前']
    total = sum(r[2] - r[1] for r in aux)
    report['aux实际调用'] = dict(次数=len(aux), 调用累计秒=total / 1e9,
        区间并集秒=sum(b-a for a,b in analyze_phase_time.merge([(r[1],r[2]) for r in aux]))/1e9,
        包围额外秒=(enveloped-total)/1e9,
        边界='相同内存计时包围真实FsyncWithDirOptions；含调用内调度，非纯fsync或SSD介质响应。')
    return report


def finish(output):
    before = shared.MODE
    shared.MODE = MODE
    try:
        result = shared.finish(output)
    finally:
        shared.MODE = before
    result['边界'] = '一对同程序独立新客机；本会话W历史准入，不将拒绝量视为节省。'
    result['因果限制'] = '核对首次拒绝前的后台文件与目标布局差异；存在提前分歧时，不将完整差额归为策略收益。'
    (output / '配对分析/完整核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    path = output / '配对分析/结果分析.md'
    text = path.read_text().replace('W-only迁移准入配对结果', 'W迁移历史准入配对结果').replace('|G2_WMIG_OFF|', '|' + MODE + '|')
    path.write_text(text + '\n' + result['因果限制'] + '\n')
    report = json.loads((output / MODE / '业务核验.json').read_text())
    attempts = report['W历史准入']['逐次']
    if attempts:
        with (output / '配对分析/W历史准入实际去向.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, list(attempts[0]))
            writer.writeheader();writer.writerows(attempts)
    return result
