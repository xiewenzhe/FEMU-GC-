#!/usr/bin/env python3
"""只读核验全块预测与同代际后续事件；观察结果不能冒充改选反事实收益。"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from analyze_all_blocks_dbbench import fields, selections, ZONE
from analyze_greedy_opportunities import clock, follow, parse

SECOND = 10**9
INFINITE = 2**64 - 1


def require(ok, why):
    if not ok:
        raise ValueError(why)


def classify(candidate, events, retired, end):
    """只跟踪原文件集合；当轮ZC、后续ZC或首次Reset均截断反事实判断。"""
    if candidate['当轮选中']:
        kind, event = '当轮ZC介入', None
        cutoff_line, cutoff_time = candidate['决策行'], candidate['决策时刻']
    else:
        kind, event = follow(candidate, events)
        cutoff_line = event['行'] if event else INFINITE
        cutoff_time = event['时刻'] if event else end
    files = candidate['文件初始字节']
    deleted = {fid: value for fid, value in retired.items()
               if fid in files and candidate['决策行'] < value[0] <= cutoff_line}
    all_retired = bool(files) and set(deleted) == set(files)
    result = dict(后续结局=kind, 后续事件行=event['行'] if event else None,
                  到事件或截止秒=cutoff_time - candidate['决策时刻'],
                  介入前全部原文件退役=all_retired,
                  介入前全部退役等待秒=max(t for _, t in deleted.values()) - candidate['决策时刻']
                  if all_retired else None,
                  介入前已退役原文件数=len(deleted), 原文件数=len(files),
                  后续RR编号=None, 后续RR有效字节=None, RR原文件子集核验=None,
                  原文件退役日志=[dict(文件编号=fid, 行=i, 等待秒=t-candidate['决策时刻'])
                                 for fid, (i, t) in sorted(deleted.items())])
    if kind == '后续同代际RR':
        copies = event['复制']
        require(event['完成'] and sum(copies.values()) == event['有效字节'], '后续RR复制不闭合')
        require(all(0 < n <= files.get(fid, 0) for fid, n in copies.items()),
                '后续RR不是原满区文件集合的子集')
        result.update(后续RR编号=event['编号'], 后续RR有效字节=event['有效字节'], RR原文件子集核验=True)
    return result


def write_csv(path, records):
    # 完整嵌套证据保存于JSON；CSV仅保留方便逐行核对的标量。
    flat = [{k: v for k, v in row.items() if not isinstance(v, (dict, list))} for row in records]
    keys = list(dict.fromkeys(k for row in flat for k in row))
    with path.open('x', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=keys)
        writer.writeheader()
        writer.writerows(flat)


def audit(source):
    matches = list(source.glob('*/blocks/run.zenfs.log'))
    require(len(matches) == 1, '无法唯一找到模型业务日志')
    log = matches[0]
    manifest = json.loads((source / '分析复核/分析来源.json').read_text())
    checked = {}
    for name in ('run.zenfs.log', 'strict.business.json', 'wrapper.exit', 'phase.txt', 'verify.log'):
        path = log.parent / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(manifest.get(str(path)) == digest, '原始归档哈希不符或未登记：' + name)
        checked[str(path)] = digest
    require((log.parent / 'wrapper.exit').read_text().strip() == '0' and
            (log.parent / 'phase.txt').read_text().strip() == 'done', '实验未正常完成')
    verification = (log.parent / 'verify.log').read_text()
    require('1048576 operations;' in verification and 'verifychecksum' in verification,
            '归档缺少全键或CRC核验')
    strict = json.loads((log.parent / 'strict.business.json').read_text())
    require(strict['启用'] == 1 and strict['历史最高RC'] <= 1024, '严格RR配置不符')
    lines = log.read_text().splitlines()
    require(not any('统一决策：' in line for line in lines), '不能混用多个最终决策格式')
    # 旧只读解析器认识统一决策；只替换最终标记，保持全部内容及日志行号不变。
    normalized = [line.replace('时序方案决策：', '统一决策：') for line in lines]
    frames, rr, events, retired = parse(normalized)
    rounds, raw_rows = selections(lines, '业务', 'blocks')
    require(len(frames) == len(rounds) and len(rr) == 14, '决策帧或RR数与本轮归档不符')
    indexed = {(r['轮次'], r['Zone']): r for r in raw_rows}
    times, representatives = {}, {}
    block_history = defaultdict(list)
    for i, line in enumerate(lines, 1):
        for marker, target in (('时间一致证据：', times), ('全块风险代表：', representatives)):
            if marker in line:
                row = fields(line, marker)
                target[int(row['轮次']), int(row['Zone']) // ZONE] = (row, i, clock(line))
        if '全块风险明细：' in line:
            row = fields(line, '全块风险明细：')
            key = (int(row['Zone']) // ZONE, int(row['代际']), int(row['块']), row['PPA'], row['会话'])
            block_history[key].append((i, int(row['轮次']), row))
    records = []
    end = clock(lines[-1])
    for frame, group in zip(frames, rounds):
        n = group['轮次']
        require(frame['轮次'] == n and '决策行' in frame, 'GC检查与最终决策无法对应')
        for z in group['安全可选Zone']:
            address = z * ZONE
            row = indexed[n, z]
            cohort = frame['文件'][address]
            diagnostic = frame['代际'][address]
            generations = list(map(int, diagnostic['主机代际'].split('/')))
            require(len(set(generations)) == 1 and diagnostic['重置窗稳定'] == '1', '候选主机代际不稳定')
            require(sum(cohort.values()) == int(row['有效字节']), '候选原文件覆盖不完整')
            prediction, prediction_line, prediction_time = representatives[n, z]
            timing = times[n, z][0]
            greedy_row = indexed[n, group['GreedyZone']]
            record = dict(轮次=n, Zone=z, 主机代际=generations[-1], 设备代际=int(row['设备代际']),
                          决策行=frame['决策行'], 决策时刻=frame['决策时刻'], 预测日志行=prediction_line,
                          预测日志时刻=prediction_time, 当轮选中=group['实际Zone'] == z,
                          GreedyZone=group['GreedyZone'], 有效字节=int(row['有效字节']),
                          相对Greedy额外有效字节=int(row['有效字节'])-int(greedy_row['有效字节']),
                          风险可知=int(row['风险可知']), 最大块RC=int(row['最大块RC']),
                          紧迫块编号=int(row['紧迫块编号']), 紧迫块RC=int(row['紧迫块RC']),
                          ETA纳秒=int(row['ETA纳秒']), 事件窗口纳秒=int(timing['事件窗口纳秒']),
                          需求概率十亿分数=int(row['需求概率十亿分数']),
                          RR预计复制字节=int(row['RR预计复制字节']), 预计留存字节=int(row['预计留存字节']),
                          全块风险完整=int(prediction['完整']), 可知块数=int(prediction['可知块数']),
                          文件初始字节=cohort)
            record.update(classify(record, events[address], retired, end))
            record['后续RR距预测日志秒'] = None
            if record['后续RR编号'] is not None:
                event = next(r for r in rr if r['编号'] == record['后续RR编号'])
                record['后续RR距预测日志秒'] = event['时刻'] - prediction_time
                # 仅是两区即时复制的乐观余量；没有为后续空间补足等成本定价。
                record['两区乐观复制余量字节'] = event['有效字节']-record['相对Greedy额外有效字节']
                maximum = int(prediction['最大RC块'])
                snapshots = [v[2] for key, values in block_history.items()
                             if key[:3] == (z, record['设备代际'], maximum)
                             for v in values if v[1] == n and v[0] < prediction_line]
                require(len(snapshots) == 1, '后续RR候选缺少当时最大RC块的原始证据')
                record['最大RC块当前证据'] = snapshots[0]
            cutoff = prediction_time + record['事件窗口纳秒'] / SECOND
            if record['当轮选中']:
                record['窗口观察结论'] = '当轮ZC介入，无法检验不清理时的RR'
            elif not record['事件窗口纳秒']:
                record['窗口观察结论'] = '未给出非零事件窗口，不能据此校准概率'
            elif record['介入前全部原文件退役'] and (
                    record['介入前全部退役等待秒'] + record['决策时刻'] <= cutoff):
                record['窗口观察结论'] = '窗口内全部原文件退役，原数据不再需要RR复制'
            elif record['后续RR编号'] is not None and (
                    prediction_time + record['后续RR距预测日志秒'] <= cutoff):
                record['窗口观察结论'] = '窗口内观察到同代际RR'
            elif prediction_time + record['事件窗口纳秒'] / SECOND <= (
                    frame['决策时刻'] + record['到事件或截止秒']):
                record['窗口观察结论'] = '窗口内未观察到同代际RR；不等同于长期无风险'
            else:
                record['窗口观察结论'] = '窗口结束前被维护或业务结束截断'
            # 后续RC仅沿同一块编号、PPA、设备会话及代际连接，禁止比较Zone最大值的差。
            if record['RR预计复制字节'] > 0 and prediction['存在'] == '1':
                wanted = [(k, values) for k, values in block_history.items()
                          if k[:3] == (z, record['设备代际'], record['紧迫块编号'])]
                current = [(key, v) for key, values in wanted for v in values if v[0] < prediction_line
                           and v[1] == n]
                require(len(current) == 1, '不能唯一定位预测代表块身份')
                key, before = current[0]
                row_block = before[2]
                cutoff_line = record['后续事件行'] or len(lines)+1
                future = [v for v in block_history[key] if prediction_line < v[0] < cutoff_line]
                record['代表块身份'] = dict(块=key[2], PPA=key[3], 会话=key[4], 设备代际=key[1])
                record['代表块当前证据'] = row_block
                record['代表块后续样本'] = [dict(轮次=k, 日志行=i, RC=int(b['RC']),
                    十秒增量=int(b['十秒增量']), 近期增量=int(b['近期增量']),
                    近期可知=int(b['近期可知'])) for i, k, b in future]
                require(all(int(b['RC']) >= int(row_block['RC']) for _, _, b in future),
                        '同块同代际RC倒退，不能继续跟踪')
            records.append(record)
    positive = [r for r in records if r['RR预计复制字节'] > 0]
    missed = [r for r in records if not r['当轮选中'] and r['后续RR编号'] is not None]
    summary = dict(总决策帧=len(rounds), 实际选区=sum(r['实际Zone'] is not None for r in rounds),
                   改选次数=sum(r['发生改选'] for r in rounds), 业务RR次数=len(rr),
                   安全候选观察数=len(records), 安全未选结局=dict(Counter(
                       r['后续结局'] for r in records if not r['当轮选中'])),
                   非零预测候选观察数=len(positive),
                   非零预测结局=dict(Counter(r['后续结局'] for r in positive)),
                   安全未选后续RR观察数=len(missed),
                   安全未选后续RR去重事件数=len({r['后续RR编号'] for r in missed}),
                   安全未选十秒内RR去重事件数=len({r['后续RR编号'] for r in missed
                       if r['后续RR距预测日志秒'] <= 10}))
    return dict(汇总=summary, 非零预测=positive, 安全未选后续RR=missed,
                全部安全候选=records, 来源哈希=checked)


def report(data):
    lines = ['# 已触发GC约束修正与上轮预测回溯', '',
        '本报告只读取2026-10-25既有业务日志，不运行新负载，不改变原实验结果。',
        '同代际是源Zone观察；日志没有逐次给出真正触发RR的块PPA，不能宣称预测代表块就是触发块。',
        '等待时间用同一客机日志时钟计算；RR开始不是精确的NAND到阈值时刻。Reset、ZC和业务结束分别截止。', '',
        '| 决策帧 | Zone | 有效MiB | ETA秒 | 事件窗口秒 | RR预计复制MiB | 后续观察 | 等待秒 |',
        '|---|---:|---:|---:|---:|---:|---|---:|']
    for r in data['非零预测']:
        outcome = r['后续结局']
        if r['介入前全部原文件退役']:
            outcome += '，原文件全部退役'
        lines.append(f"| {r['轮次']} | {r['Zone']} | {r['有效字节']/(1<<20):.3f} | "
            f"{r['ETA纳秒']/SECOND:.3f} | {r['事件窗口纳秒']/SECOND:.3f} | "
            f"{r['RR预计复制字节']/(1<<20):.3f} | {outcome} | "
            f"{r['到事件或截止秒']:.3f} |")
    lines += ['', '“业务结束截尾”只表示有限观察结束；不是永远不会RR。预测窗口内ZC介入不能作为反事实RR预测失败证据。',
        '后续ZC若发生在原预测窗口结束之后，则此前已完整观察的窗口仍可用于核对近期RR是否出现。', '',
        '| 决策帧 | Zone | 预计RR复制MiB | 后续RR等待秒 | 实际RR复制MiB | 风险可知 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in data['安全未选后续RR']:
        if r['后续RR距预测日志秒'] > 10:
            continue
        lines.append(f"| {r['轮次']} | {r['Zone']} | {r['RR预计复制字节']/(1<<20):.3f} | "
            f"{r['后续RR距预测日志秒']:.3f} | {r['后续RR有效字节']/(1<<20):.3f} | {r['风险可知']} |")
    lines += ['', '同一次RR可能被多轮观察，统计按RR编号去重。两区乐观复制余量仅为实际RR复制减相对Greedy额外有效量，',
        '没有计入后续补足空间、目标区再次RR、源读取代价等，因此不能冒充实际可避免成本。', '',
        '已确认的具体误差：第4帧Zone56预计有效数据仍可存活约5.69秒，但全部原文件约2.93秒后退役；',
        '第26帧Zone40预计约31.01秒，实际约0.66秒后全部退役。线性失效速度没有预见文件集中退役。',
        '第7帧Zone56的同一物理块78由RC684增长到下一帧803，随后近期增量为0；原约6.53秒ETA未兑现，',
        '并且之后约256秒没有同代际RR或ZC。第18帧Zone63的同一块87在下一帧仍为847，近期增量为0；',
        '后续ZC约72.49秒后才介入，不能把它当成原10秒窗口内的RR已被干预。',
        '这些是持续读外推与留存外推误差，不能靠增加累计RC权重修复，也不能据少量单次事件校准泊松概率。', '',
        '两个零估计但十秒内RR的例子：第9帧Zone11全部128块风险不可知，原最大RC块107可见前端点仅约0.225秒，',
        '不足原2秒历史要求；只能确认历史证据不足，历史缺失的具体原因尚未追溯。第25帧Zone43当时同块近期增量为0，',
        '后来约7.57秒发生RR；这是近期停读后又有读取增长的情况，不能把事后增长当作当时已有预测信息。', '',
        '第1帧不执行的路径已确认：外层用取整后的空闲百分比判断是否进入GC（日志显示20%），',
        '内层按精确20%字节目标计算缺口。外层检查空闲1687561216字节、非空闲6499509400字节，',
        '内层重新采样后空闲1685464064字节，仍大于目标1636994693字节，所以缺口为零。',
        '修正保留原外层触发，不把内部空间目标作为取消已触发GC的第二次判断。', '',
        '代码修正独立保存为zenfs-triggered-gc.patch及rrzc_triggered_gc.h：仅在全块模型已触发GC的调用处，',
        '将零需求转换为一字节正进展标记；原始缺口仍记录为零，另记评估需求。既有正缺口、安全检查、',
        'Greedy、旧选择器和RR路径不变。补丁须与头文件一起部署，不能只复制头文件就声称已生效。',
        '该修正已做本地C++边界编译及真实源补丁往返；尚未重编客机完整ZenFS，也没有修正后的业务实验结果。', '',
        '本轮不据事后失效时刻建立线上预言器，也不为了出现改选扩大RR抵扣。下一项模型工作应先验证',
        '“近期读取还会持续多久”和“有效数据会不会快速退役”的估计误差；短历史不可知与停读后重新升温应分别处理。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = audit(args.source)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, rows in (('非零预测后续.csv', data['非零预测']),
                       ('安全未选后续RR.csv', data['安全未选后续RR'])):
        write_csv(args.output / name, rows)
    with (args.output / '预测回溯.json').open('x') as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
    with (args.output / '预测回溯.md').open('x') as out:
        out.write(report(data))
    print(json.dumps(data['汇总'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
