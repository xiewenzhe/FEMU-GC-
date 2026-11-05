"""用已验收事务和extent追踪RR空间贡献；不模拟提前ZC的反事实。"""
import argparse
from datetime import datetime
import csv
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from ycsb_af import digest, require

ZONE = 128 << 20


def parse_rr_start(line):
    match = re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节，封闭剩余容量 (\d+) 字节', line)
    require(match is not None, 'RR开始字段缺失')
    return dict(源Zone=int(match[1]), 达阈值物理块数量=int(match[2]), 有效字节=int(match[3]),
                源Finish损失字节=int(match[4]), 实际触发块编号=None)


def net_space(capacity, source_tail, destination_consumed):
    """局部尾部可写空间账：Reset增量减去源Finish和目标写指针推进。"""
    require(0 <= source_tail <= capacity and 0 <= destination_consumed <= capacity, '空间输入越界')
    return capacity - source_tail - destination_consumed


def copy_key(row, destination=False):
    return (int(row['文件编号']), int(row['目标片段' if destination else '源片段']), int(row['有效字节']))


def analyze(source, output):
    s = json.loads((source / '状态.json').read_text())
    a = json.loads((source / '阶段归档.json').read_text())
    repeated = json.loads((source / 'Greedy重复核验.json').read_text())
    require(a['完整'] and a['业务成功'] and not a['客机仍存活'] and s['当前客机PID'] is None
            and repeated['通过'], '来源未完整验收')
    require(digest(Path(s['镜像处理']['archive'])) == s['镜像处理']['archive_sha256'], '原归档不同')
    source_code = source / s['标签'] / '协同池副本/plugin/zenfs/fs/fs_zenfs.cc'
    code = source_code.read_text()
    require(re.search(r'"RR 开始：[^\n]+\n\s*zone_it->start, zone_pressure.blocks_at_threshold,', code),
            'RR日志含义不匹配，不能把数量当编号')
    results = {}; exported = []
    for tag in ('p1', 'p2'):
        root = source / '逐臂采证' / tag
        log = root / 'run.zenfs.log'; lines = log.read_text().splitlines()
        verified = json.loads((source / (tag + '-核验.json')).read_text())
        tasks = verified['事务']
        # 独立审计重新计算全部维护，拒绝仅信任派生JSON。
        import tempfile
        from audit_colocation_transactions import audit
        with tempfile.TemporaryDirectory() as tmp:
            derived = Path(tmp)
            (derived / 'zenfs.log').write_text('受控放置业务开始：派生窗口\n' + log.read_text() + '\n受控放置业务截止：派生窗口\n')
            real = audit(derived)
        require(real['事务'] == tasks and real['维护账目'] == verified['维护'], '实际账目重算不符')
        matches = [i for i, t in enumerate(tasks) if t['类型'] == 'RR' and t['Zone'] == 9 * ZONE
                   and t['迁移字节'] == 21145689]
        require(len(matches) == 1, '关键RR不是唯一，需重新指定案例')
        first_index = matches[0]; first = tasks[first_index]
        current = {copy_key(c['字段'], True) for c in first['复制']}
        require(len(current) == len(first['复制']), '迁移片段重复')
        later = []
        for t in tasks[first_index + 1:]:
            matched = [c for c in t['复制'] if copy_key(c['字段']) in current]
            if not matched:
                continue
            require(len(matched) == len(current) == len(t['复制']), '链条发生分裂或混入新片段，不能套用整区账')
            for c in matched:
                q = c['字段']
                previous = next(p for p in (first if not later else later[-1])['复制']
                                if copy_key(p['字段'], True) == copy_key(q))
                require(previous['字段']['目标Zone'] == q['源Zone'], '片段来源Zone不符')
                lo, hi = previous['行'] - 1, c['行'] - 1
                resets = [l for l in lines[lo:hi] if 'Zone 重置诊断：Zone=' + q['源Zone'] + '，状态=成功' in l]
                require(not resets, '后继片段跨过Reset，不能认作同一数据实例')
            later.append(t); current = {copy_key(c['字段'], True) for c in t['复制']}
        require(len(later) == 1 and later[0]['类型'] == 'RR', '实际后续链不同，拒绝预设两次RR')
        chain = [first, *later]; chain_rows = []
        for ordinal, task in enumerate(chain, 1):
            start = parse_rr_start(task['原始选择行'])
            copies = [c['字段'] for c in task['复制']]
            targets = {int(c['目标Zone']) for c in copies}
            require(len(targets) == 1, '目标分裂，不能按单空目标重算')
            target = targets.pop(); first_target = min(int(c['目标片段']) for c in copies)
            require(first_target == target, '目标不是从Zone首地址写入')
            consumed = 0
            for c in sorted(copies, key=lambda c: int(c['目标片段'])):
                require(int(c['目标片段']) == target + consumed, '目标写入不连续')
                consumed += (int(c['有效字节']) + 511) // 512 * 512
            take = [l for l in lines[task['开始行'] - 1:task['结束行']]
                    if '协同池取得目标：' in l and 'Zone=' + str(target) + '，' in l]
            require(take and '申请空目标' in take[0], '缺少空目标分配证据')
            row = dict(序号=ordinal, 类型=task['类型'], 源Zone=task['Zone'] // ZONE,
                源日志Reset序数=task['源代际'], 目标Zone=target // ZONE, 有效迁移字节=task['迁移字节'],
                目标WP推进字节=consumed, 对齐开销字节=consumed - task['迁移字节'],
                源Finish损失字节=start['源Finish损失字节'], Reset释放字节=ZONE,
                局部净可写空间增量字节=net_space(ZONE, start['源Finish损失字节'], consumed),
                起时=task['原始选择行'].split()[0], 止时=lines[task['结束行'] - 2].split()[0],
                达阈值物理块数量=start['达阈值物理块数量'], 实际触发块编号=None,
                原始事务=task, 空目标分配证据=take[0])
            chain_rows.append(row); exported.append(dict(臂=tag, **{k: v for k, v in row.items() if k not in ('原始事务', '空目标分配证据')}))
        last = chain[-1]
        terminal = list(csv.DictReader((root / '布局-61.extent.csv').open()))
        for c in last['复制']:
            q = c['字段']
            require(any(e['文件'] == q['文件名'] and e['Zone'] == q['目标Zone']
                        and e['起点'] == q['目标片段'] and e['字节'] == q['有效字节'] for e in terminal), '末端extent不匹配')
        terminal_zones = json.loads((root / 'zones.business.json').read_text())['zone_list']
        dest = chain_rows[-1]['目标Zone']; z = terminal_zones[dest]
        require(z['slba'] * 512 == dest * ZONE and (z['wp'] - z['slba']) * 512 == chain_rows[-1]['目标WP推进字节'], '末端真实WP未确认对齐推进')
        before = next(c for c in verified['逐轮选择'] if c['字段']['轮次'] == 12)
        plans = before['候选方案']
        parse = lambda text: datetime.strptime(text, '%Y/%m/%d-%H:%M:%S.%f')
        checkpoint = 0; risk_path = []
        for number, line in enumerate(lines, 1):
            if '受控GC检查点开始：批次=' in line:
                checkpoint = int(line.split('受控GC检查点开始：批次=')[1])
            if 'RR风险状态：' in line and 52 <= checkpoint <= 60:
                row = fields(line, 'RR风险状态：')
                if int(row['Zone']) in (7 * ZONE, 9 * ZONE):
                    risk_path.append(dict(检查点=checkpoint, 行=number, **row))
        require(risk_path, '缺少维护链后的风险轨迹')
        results[tag] = dict(维护链=chain_rows, 同片段两次有效复制字节=sum(t['迁移字节'] for t in chain),
            第一次RR完成至第二次开始秒=(parse(chain_rows[1]['起时']) - parse(chain_rows[0]['止时'])).total_seconds(),
            全部首区方案均含Zone9=all(9 * ZONE in p['计划区'] for p in plans),
            第12轮原方案=plans, 最终目标状态=z, 后续Zone风险轨迹=risk_path,
            原日志SHA256=digest(log), 起点与末端空间=json.loads((root / 'space.business.json').read_text()))
    require(not output.exists(), '不覆盖结果')
    output.mkdir(parents=True)
    result = dict(来源=str(source), 完整归档SHA256=s['镜像处理']['archive_sha256'], 各臂=results,
        RR日志语义源码=str(source_code), RR日志语义源码SHA256=digest(source_code),
        分析源码SHA256=digest(Path(__file__)),
        边界='局部可写空间从实际Reset/源Finish与按512字节对齐目标推进重算，非全设备原子测量；不含元数据写、其他Zone并发变化。两次复制实存不等于提前ZC可省；未做反事实对照。')
    (output / '同片段维护链与空间.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    with (output / '两次RR空间账.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(exported[0])); writer.writeheader(); writer.writerows(exported)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('source', type=Path); p.add_argument('output', type=Path)
    args = p.parse_args(); r = analyze(args.source, args.output)
    print(json.dumps({t: {k: v for k, v in x.items() if k not in ('维护链', '第12轮原方案', '起点与末端空间')}
                      for t, x in r['各臂'].items()}, ensure_ascii=False, indent=2))
