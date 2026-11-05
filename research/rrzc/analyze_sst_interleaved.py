"""核验预注册交错请求与检查点，旧分阶段负载分析保持不变。"""
from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import request_rows
from analyze_sst_lifetime import persistent_mode
from ycsb_af import require
import re


def planned_requests(smoke=False):
    windows, width, hot, batch = (1, 256, 16, 32) if smoke else (6, 65536, 4096, 8192)
    for window in range(windows):
        for part in range(2):
            reads = 0
            for n in range(width - hot):
                yield 1, window * width + hot + n
                due = (n + 1) * (4 * batch) // (width - hot)
                while reads < due:
                    yield 0, (part * 4 * batch + reads) % hot
                    reads += 1


def checked_requests(rows, smoke=False):
    expected = iter(planned_requests(smoke)); counts = [0, 0]; total = 0
    for row in rows:
        planned = next(expected, None)
        require(planned is not None and row[:4] == (total, planned[0], counts[planned[0]], planned[1]),
                '实际请求与预注册交错首差：序号=' + str(total))
        counts[row[1]] += 1; total += 1; yield row
    require(next(expected, None) is None, '交错请求提前结束')


def timing(root):
    text = (root / 'run.log').read_text()
    require(persistent_mode(text) and text.count('SST生命周期交错：启用=1') == 1, '固定H交错未启用')
    stages = [fields(l, 'SST生命周期阶段：') for l in text.splitlines() if 'SST生命周期阶段：' in l]
    expected = []; count = 0
    for window in range(6):
        for part in range(2):
            for batch in range(4):
                count += 15360 + 8192
                expected.append(('交错H片段完成', window, part * 4 + batch, count))
            expected.append(('交错W覆盖完成', window, part, count))
    require([int(s['序号']) for s in stages] == list(range(1, 61)), '交错60检查点缺失')
    require([(s['阶段'], int(s['窗口']), int(s['段']), int(s['成功请求'])) for s in stages] == expected,
            '交错检查点请求边界不符')
    marks = [(int(i), int(n)) for i, n in re.findall(r'校准阶段边界：序号=(\d+)，纳秒=(\d+)', text)]
    require([i for i, _ in marks] == list(range(6)), '方法边界不完整')
    first = last = None
    for row in checked_requests(request_rows(root / '请求提交.bin')):
        if first is None: first = row[4]
        last = row[6]
    start, end = [int((root / n).read_text()) for n in ('run.start.ns', 'run.end.ns')]
    times = [n for _, n in marks]
    require(start <= times[0] <= times[1] <= first <= last <= times[2] <= times[3] <= times[4] <= times[5] <= end,
            '交错方法时钟不闭合')
    return dict(请求首尾秒=(last-first)/1e9, 请求窗口吞吐ops=1130496/((last-first)/1e9),
        整阶段秒=(end-start)/1e9, 整阶段吞吐ops=1130496/((end-start)/1e9),
        方法包含首尾维护秒=(times[2]-times[1])/1e9, 固定H=True, 持续交错=True,
        逐条请求符合预注册=True, 口径='单前台closed-loop交错；含Flush及维护等待，不设QPS限制；非正常并发性能')


def analyze(output):
    """只分析已退出归档；复用生命周期关联器，不借用其旧请求顺序。"""
    import json
    from pathlib import Path
    from analyze_sst_lifetime import arm as opportunity_arm
    from analyze_sampling_scope import suggestion_difference
    from ycsb_af import digest
    state = json.loads((output/'状态.json').read_text())
    archive = json.loads((output/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
            and state['当前客机PID'] is None, '实验未完整归档退出')
    require(digest(Path(state['镜像处理']['archive'])) == state['镜像处理']['archive_sha256'], '归档不同')
    labels = json.loads((output/'源码/受控标签来源.json').read_text())['标签']; result = {}
    for tag in state['已完成']:
        root = output/'逐臂采证'/tag
        verified = json.loads((output/(tag+'-核验.json')).read_text())
        evidence = opportunity_arm(root, labels)
        log = (root/'run.zenfs.log').read_text(); rows = []
        for number, line in enumerate(log.splitlines(), 1):
            if '全块代表门控：' in line:
                rows.append(dict(行=number, **fields(line, '全块代表门控：')))
        result[tag] = dict(维护=verified['维护'], 计时=timing(root), API延迟=verified['API延迟'],
            机会=evidence, 代表块门控=rows,
            有效候选增长快照数=sum(r['近期可知']=='1' and int(r['近期增量'])>0 for r in rows),
            影子建议差异数=sum(suggestion_difference(r['字段']) for r in verified['逐轮选择']),
            原日志SHA256=digest(root/'run.zenfs.log'))
    return dict(各臂=result, 完整归档SHA256=state['镜像处理']['archive_sha256'],
                边界='随后RR是回溯关联，不等于可避免复制；需核对实际改选、维护链及末端空间。')


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path); p.add_argument('output', type=Path)
    args = p.parse_args(); require(not args.output.exists(), '不覆盖派生结果')
    result = analyze(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({t:dict(维护=r['维护'], 候选=r['机会']['候选快照'],
        在线正RR成本=r['机会']['正RR预计成本数'], 候选随后RR=r['机会']['涉及独立RR数'],
        候选增长=r['有效候选增长快照数'], 影子差异=r['影子建议差异数'])
        for t,r in result['各臂'].items()}, ensure_ascii=False, indent=2))
