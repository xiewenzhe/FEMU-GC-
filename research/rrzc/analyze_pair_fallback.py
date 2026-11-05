"""追踪准入回退的实际后果；未来复制只用于回溯，不能充当在线反事实。"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import tempfile

from analyze_pair_admission import choices, fields
from analyze_flush_retirement import resolve_task, next_generation_task
from analyze_reproducibility import stamp
from analyze_sst_compaction import events
from audit_colocation_transactions import audit
from ycsb_af import digest, require

ZONE = 128 << 20


def partition(copies, hot, target, first_hot_start):
    """同区同代际顺序写中，仅统计下一次维护实际仍有效的非H片段。"""
    result = dict(H之前写入且届时仍有效字节=0, H之后写入且届时仍有效字节=0)
    for item in copies:
        q = item['字段']
        require(int(q['源Zone']) == target, '后继维护源区不同')
        start = int(q['源片段']); size = int(q['有效字节'])
        require(size > 0 and target <= start < start + size <= target + ZONE,
                '后继源片段超出Zone')
        if q['文件名'] in hot:
            continue
        require(not start < first_hot_start < start + size,
                '伴随片段跨过H物理起点，不能按顺序写分类')
        kind = 'H之前写入且届时仍有效字节' if start < first_hot_start else 'H之后写入且届时仍有效字节'
        result[kind] += size
    return result


def trace_arm(path, proof, hot, arm, enforce):
    log = path / 'run.zenfs.log'; lines = log.read_text().splitlines()
    # 先从原日志重算事务；原审计业务窗的包装行号不得直接当原日志行号。
    with tempfile.TemporaryDirectory(prefix='rrzc-pair-fallback-') as tmp:
        local = Path(tmp)
        (local / 'zenfs.log').write_text('受控放置业务开始：派生窗口\n' + log.read_text() +
                                       '\n受控放置业务截止：派生窗口\n')
        checked = audit(local)
    require(checked['事务'] == proof['事务'] and checked['维护账目'] == proof['维护'],
            '原始事务与已归档账目不同')
    tasks = [resolve_task(lines, task) for task in proof['事务']]
    resets = defaultdict(list)
    for n, line in enumerate(lines, 1):
        if 'Zone 重置诊断：' in line:
            f = fields(line, 'Zone 重置诊断：')
            require(f['状态'] == '成功', '存在失败Reset')
            resets[int(f['Zone'])].append(n)
    decisions, _ = choices(log, enforce)
    rejected = []; paths = []; pieces = []; classified = Counter()
    records = events(path / 'run.rocksdb.log')
    origins = {k: {e['记录']['job'] for e in records if e['记录'].get('event') == v}
               for k, v in (('Flush', 'flush_started'), ('Compaction', 'compaction_started'))}
    created = {int(e['记录']['file_number']): e['记录']['job'] for e in records
               if e['记录'].get('event') == 'table_file_creation' and e['记录'].get('status') == 'OK'}
    require(not origins['Flush'] & origins['Compaction'], '创建任务来源重叠')

    def category(q):
        if q['文件名'] in hot:
            return 'H'
        job = created.get(int(Path(q['文件名']).stem))
        return next((name for name, jobs in origins.items() if job in jobs), '其他或初始文件')

    by_task = defaultdict(list)
    for decision in decisions:
        if not enforce or decision['允许'] != '0':
            continue
        n = decision['日志行']; tid = lines[n - 1].split()[1]
        owner = [(i, t) for i, t in enumerate(tasks, 1) if t['原始开始行'] < n < t['原始结束行']
                 and t['原始选择行'].split()[1] == tid]
        require(decision['阶段'] == '必要迁移' and len(owner) == 1,
                '出现新文件拒绝或未关联维护；不能套用H迁移解释')
        ordinal, task = owner[0]
        following = [c for c in task['复制'] if c['原始行'] > n and
                     lines[c['原始行'] - 1].split()[1] == tid]
        require(following, '拒绝后没有复制')
        c = following[0]; q = c['字段']
        require(q['文件名'] in hot, '拒绝后的下一复制不是已知H')
        require(not any(n < d['日志行'] < c['原始行'] and
                        lines[d['日志行'] - 1].split()[1] == tid for d in decisions),
                '两次准入决定跨越同一复制，无法唯一关联')
        target = int(q['目标Zone'])
        allocations = [fields(line, '公共内容分配：') for line in lines[n:c['原始行'] - 1]
                       if '公共内容分配：阶段=迁移' in line and line.split()[1] == tid]
        require(allocations and int(allocations[-1]['Zone']) == target,
                '普通分配与复制实际落点不同')
        row = dict(臂=arm,准入序号=int(decision['序号']),维护序号=ordinal,
                   被拒ZoneID=decision['ZoneID'],实际目标ZoneID=target // ZONE,
                   文件=q['文件名'],有效复制字节=int(q['有效字节']),原因=decision['原因'],
                   被拒目标当时非H有效字节=int(decision['账目有效']) - int(decision['H']),
                   被拒目标当时W字节=int(decision['W']),拒绝原始行=n,复制原始行=c['原始行'])
        rejected.append(row); by_task[ordinal].append(row)

    for ordinal, task in enumerate(tasks, 1):
        hot_copies = [c for c in task['复制'] if c['字段']['文件名'] in hot]
        if not hot_copies:
            continue
        targets = {int(c['字段']['目标Zone']) for c in hot_copies}
        require(len(targets) == 1, 'H迁入多个目标，须按片段拆分分析')
        target = targets.pop()
        first = min(hot_copies, key=lambda c: c['原始行'])
        cutoff = int(first['字段']['目标片段'])
        epoch = bisect_right(resets[target], first['原始行'])
        require(all(bisect_right(resets[target], c['原始行']) == epoch for c in hot_copies),
                '本次H迁入跨过目标Reset')
        following = next_generation_task(tasks, task['原始结束行'], target, epoch)
        window = partition(following['复制'], hot, target, cutoff) if following else {
            'H之前写入且届时仍有效字节': None, 'H之后写入且届时仍有效字节': None}
        after_ordinal = tasks.index(following) + 1 if following else None
        row = dict(臂=arm,维护序号=ordinal,源ZoneID=task['Zone'] // ZONE,目标ZoneID=target // ZONE,
                   目标日志Reset序数=epoch,H首复制原始行=first['原始行'],H首写入物理偏移字节=cutoff - target,
                   本轮拒绝申请数=len(by_task.get(ordinal, [])),后继维护序号=after_ordinal,
                   后继RR间隔秒=(stamp(following['原始选择行']) - first['时刻']) / 1e9 if following else None,
                   后继总迁移字节=following['迁移字节'] if following else None,**window,
                   结局='同代际后继维护已观察' if following else '观察窗内无同代际后继维护')
        paths.append(row)
        if not following:
            continue
        arrived = {(c['字段']['文件名'], c['字段']['目标片段'], c['字段']['有效字节']) for c in hot_copies}
        observed = {(c['字段']['文件名'], c['字段']['源片段'], c['字段']['有效字节'])
                    for c in following['复制'] if c['字段']['文件名'] in hot}
        require(arrived == observed, '后继H片段与本次成功迁入不闭合，须另查中间映射变化')
        for c in following['复制']:
            q = c['字段']; kind = category(q)
            if kind == 'H':
                continue
            before = int(q['源片段']) < cutoff
            side = 'H之前写入' if before else 'H之后写入'
            classified[(side, kind)] += int(q['有效字节'])
            pieces.append(dict(臂=arm,H迁入维护序号=ordinal,后继维护序号=after_ordinal,目标ZoneID=target // ZONE,
                               目标日志Reset序数=epoch,文件=q['文件名'],来源=kind,位置分类=side,
                               后继源片段=int(q['源片段']),后继复制字节=int(q['有效字节']),
                               后继复制原始行=c['原始行']))
    require(sum(r['本轮拒绝申请数'] for r in paths) == len(rejected), '拒绝申请未全部关联H路径')
    summary = dict(实际拒绝申请数=len(rejected),含拒绝的维护数=sum(bool(v) for v in by_task.values()),
                   已观察后继路径数=sum(r['后继维护序号'] is not None for r in paths),
                   未观察后继路径数=sum(r['后继维护序号'] is None for r in paths),
                   后继伴随复制分类={side + '/' + kind: size for (side, kind), size in classified.items()},
                   已观察伴随复制字节=sum(classified.values()),
                   输入SHA256={n:digest(path / n) for n in ('run.zenfs.log','run.rocksdb.log')})
    return summary, rejected, paths, pieces


def write_csv(path, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('x', newline='') as stream:
        out = csv.DictWriter(stream, keys); out.writeheader(); out.writerows(rows)


def analyze(source, output):
    require(not output.exists(), '不覆盖已有结果')
    verified = json.loads((source / '配对准入核验.json').read_text())
    require(verified['通过'], '准入四臂原核验未通过')
    root = Path(verified['来源']); state = json.loads((root / '状态.json').read_text())
    archive = json.loads((root / '阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and
            state['当前客机PID'] is None, '来源未归档退出')
    require(digest(Path(state['镜像处理']['archive'])) == verified['完整归档SHA256'], '原归档哈希改变')
    hot = json.loads((root / '源码/受控标签来源.json').read_text())['标签']
    summaries = {}; rejected = []; paths = []; pieces = []
    for arm in ('p1', 'p2', 'm1', 'm2'):
        proof = json.loads((root / (arm + '-核验.json')).read_text())
        require(proof['改选次数'] == 0, '出现victim改选，不能只归因放置')
        summary, a, b, c = trace_arm(root / '逐臂采证' / arm, proof, hot, arm, int(arm.startswith('m')))
        summaries[arm] = summary; rejected += a; paths += b; pieces += c
    # 比较真实分类和物理路径，不要求墙钟时间及文本行号相等。
    def semantic(rows, arm, excluded):
        return [{k:v for k,v in row.items() if k not in excluded | {'臂'}} for row in rows if row['臂'] == arm]
    for left, right in (('p1', 'p2'), ('m1', 'm2')):
        for rows, excluded in ((rejected, {'拒绝原始行','复制原始行'}),
                               (paths, {'H首复制原始行','后继RR间隔秒'}),
                               (pieces, {'后继复制原始行'})):
            require(semantic(rows, left, excluded) == semantic(rows, right, excluded), '两次回退/后继路径语义不同')
    output.mkdir(parents=True)
    write_csv(output / '逐次拒绝实际落点.csv', rejected)
    write_csv(output / 'H迁入与后继维护.csv', paths)
    write_csv(output / '后继伴随数据写入顺序.csv', pieces)
    result = dict(通过=True,来源=str(root),来源准入核验SHA256=digest(source / '配对准入核验.json'),
                  完整归档SHA256=verified['完整归档SHA256'],各臂=summaries,路径语义独立重复一致=True,
                  分析脚本SHA256=digest(Path(__file__)),
                  边界='物理顺序分类仅描述下一次同代际维护仍有效的片段；之前写入字节不是H迁入当时全部有效量；之后写入包括新写及必要迁移。无后继为删失。不得用事后存活量当在线输入或独立反事实收益。')
    (output / '回退路径核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (output / '分析脚本.py').write_bytes(Path(__file__).read_bytes())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.source, args.output)['各臂'], ensure_ascii=False, indent=2))
