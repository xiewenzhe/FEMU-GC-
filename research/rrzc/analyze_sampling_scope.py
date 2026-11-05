"""核验采样修正的真实门控；不改变输入或推断反事实收益。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from ycsb_af import digest, require

ZONE = 128 << 20


def suggestion_difference(row):
    """未给出建议时忽略Zone占位符，先比较存在性。"""
    a, b = row['模型存在'], row['Greedy存在']
    require(a in (0, 1) and b in (0, 1), '建议存在性非法')
    return a != b or bool(a and row['模型Zone'] != row['GreedyZone'])


def analyze(source, output):
    state = json.loads((source / '状态.json').read_text())
    archive = json.loads((source / '阶段归档.json').read_text())
    opportunity = json.loads((source / '生命周期机会核验.json').read_text())
    build = json.loads((source / '生命周期构建核验.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
            and state['当前客机PID'] is None and opportunity['重复']['通过'], '来源未完整验收退出')
    require(digest(Path(state['镜像处理']['archive'])) == opportunity['完整归档SHA256'], '归档不同')
    require(build['仅重编'] == ['fs_zenfs.cc'] and build['请求前端对象逐字复用']
            and build['模型与原库未修改'], '构建边界不同')
    result = dict(来源=str(source), 完整归档SHA256=opportunity['完整归档SHA256'],
                  构建=build, 各臂={}, 分析源码SHA256=digest(Path(__file__)),
                  边界='仅Greedy采样诊断；最大RC代表块不等于最早ETA块；后续RR不等于可避免复制。')
    exported = []
    for tag in ('p1', 'p2'):
        log = source / '逐臂采证' / tag / 'run.zenfs.log'
        text = log.read_text(); lines = text.splitlines()
        rows = []; distributions = {}; costs = {}; missing = []
        for number, line in enumerate(lines, 1):
            if '全块代表门控：' in line:
                row = fields(line, '全块代表门控：')
                row = {k: int(v) if v.isdecimal() else v for k, v in row.items()}
                row.update(行=number, 时刻=line.split()[0], ZoneID=row['Zone'] // ZONE)
                rows.append(row); exported.append(dict(臂=tag, **row))
            if '全块代表缺帧：' in line:
                missing.append(dict(行=number, **fields(line, '全块代表缺帧：')))
            if '全块风险代表：' in line:
                row = fields(line, '全块风险代表：')
                distributions[(int(row['轮次']), int(row['Zone']))] = row
            if '时序成本：' in line:
                row = fields(line, '时序成本：')
                costs[(int(row['轮次']), int(row['Zone']))] = row
        require(rows and '全块采样范围：未满区=1，代表诊断=1' in text, '实际诊断未生效')
        index = {(r['轮次'], r['Zone']): r for r in rows}
        require(len(index) == len(rows), '候选代表块诊断重复')
        linked = []
        for event in opportunity['各臂'][tag]['候选随后RR详情']:
            candidate = event['候选']; key = (candidate['轮次'], candidate['Zone'])
            require(key in index and key in costs and key in distributions, '后续RR候选缺少代表块证据')
            raw = lines[event['下一次维护']['行'] - 1]
            match = re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，', raw)
            require(match and int(match[1]) == candidate['Zone'], 'RR源区原始行不符')
            linked.append(dict(实际关联=event, 代表块=index[key], 全块汇总=distributions[key],
                               成本=costs[key], 达阈值物理块数量=int(match[2]),
                               实际触发块编号=None, RR原始行=raw))
        verified = json.loads((source / (tag + '-核验.json')).read_text())
        result['各臂'][tag] = dict(代表快照数=len(rows), 缺帧=missing,
            代表近期可知=sum(r['近期可知'] for r in rows),
            代表增长原因=dict(Counter(r['增长原因'] for r in rows)),
            后续同代际RR=linked, 正RR预计成本数=opportunity['各臂'][tag]['正RR预计成本数'],
            维护=verified['维护'], 改选次数=verified['改选次数'],
            影子建议差异数=sum(suggestion_difference(r['字段']) for r in verified['逐轮选择']),
            双方均无建议轮数=sum(not r['字段']['模型存在'] and not r['字段']['Greedy存在']
                                 for r in verified['逐轮选择']),
            计时=verified['计时'], API延迟=verified['API延迟'],
            末端空间={k: v for k, v in verified['末端空间'].items() if k != 'Zone'},
            原日志SHA256=digest(log))
    require(not output.exists(), '不覆盖派生结果')
    output.mkdir(parents=True)
    (output / '采样门控与真实维护.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    with (output / '全部候选代表块.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(exported[0]))
        writer.writeheader(); writer.writerows(exported)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = analyze(args.source, args.output)
    print(json.dumps({tag: {k: r[k] for k in ('代表快照数', '代表近期可知', '代表增长原因',
                     '正RR预计成本数', '维护', '影子建议差异数')}
                     for tag, r in result['各臂'].items()}, ensure_ascii=False, indent=2))
