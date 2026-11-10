"""离线追踪三份W文件首次混放的extent路径；不启动业务或改变放置规则。"""
import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from analyze_placement_trace import rows, require

MODES = ('G2', 'G2_WSURVIVOR')
FILES = (988, 1051, 1052)
MIB = 1 << 20
ZONE = 1 << 27


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * MIB), b''):
            result.update(block)
    return result.hexdigest()


def analyze(pair, previous, output):
    require(not output.exists(), '不覆盖已有派生结果')
    require(json.loads((pair/'状态.json').read_text())['完整实验完成'], '原配对未完成')
    proof = json.loads((previous/'实际去向与首差核验.json').read_text())
    source_check = json.loads((previous/'影子归因结论.json').read_text())
    require(all(x['创建来源及序号范围一致'] for x in source_check['门控文件跨臂实际复制']),
            '门控文件创建来源不同，不能直接对照编号')
    with (previous/'逐笔复制分类.csv').open(encoding='utf-8-sig') as stream:
        copies = list(csv.DictReader(stream))
    result, timelines, evidence = [], {}, {}
    for mode in MODES:
        report = json.loads((pair/mode/'业务核验.json').read_text())
        raw = Path(proof['各臂'][mode]['原始目录'])
        evidence[mode] = {}
        for name, expected in proof['各臂'][mode]['原始SHA256'].items():
            actual = digest(raw/name)
            require(actual == expected, '原始来源哈希与上一阶段不符：' + name)
            evidence[mode][str(raw/name)] = actual
        events = list(rows(raw/'组成.bin'))
        at = {r[0]: r for r in events}
        cutoff = max(r[0] for r in events if r[3] == 27 and r[7] == 2)
        selected = [r for r in copies if r['模式'] == mode]
        owners = {t['文件编号']: t['对象'] for t in report['组成']['目标']
                  if t.get('文件编号') in FILES}
        changes = defaultdict(list)
        for change in report['组成']['组成变化']:
            changes[(change['Zone'], change['代际'])].append(change)
        for key in changes:
            changes[key].sort(key=lambda x: x['事件'])
        primary = set()
        initial = defaultdict(list)
        migrating = set()
        retired, cleared = defaultdict(list), defaultdict(list)
        event_owner = {owner: fid for fid, owner in owners.items()}
        for r in events:
            seq, _, _, kind, _, owner, _ = r[:7]
            if owner not in event_owner:
                continue
            if kind == 2 and not r[11]:
                primary.add(owner)
            elif kind == 5:
                migrating.add(owner)
            elif kind == 6:
                migrating.discard(owner)
            elif kind == 3 and owner in primary and owner not in migrating:
                initial[event_owner[owner]].append(r)
            elif kind == 16 and seq < cutoff:
                retired[owner].append(seq)
            elif kind == 4 and seq < cutoff:
                cleared[owner].append(seq)
        mode_records = []
        for fid in FILES:
            require(initial[fid], '缺少文件原始extent')
            own = owners[fid]
            first = min(initial[fid], key=lambda r: r[0])
            retirement = next(iter(retired[own]), None)
            release = next((s for s in cleared[own] if retirement and s > retirement), None)
            require((retirement is None) == (release is None), '退役与释放需要进一步区分核验')
            for index, add in enumerate(initial[fid], 1):
                seq, ns = add[:2]
                zone, generation, start, length = add[7:11]
                chain = []
                current = (zone, generation, start)
                for c in selected:
                    if int(c['文件编号']) != fid or int(c['组成事件']) <= seq:
                        continue
                    if (int(c['源Zone']), int(c['源代际']), int(c['源片段'])) != current:
                        continue
                    require(int(c['有效复制字节']) == length, '片段被拆分，不能沿用完整片段链')
                    chain.append(c)
                    current = (int(c['目标Zone']), int(c['目标代际']), int(c['目标片段']))
                first_copy = int(chain[0]['组成事件']) if chain else None
                last = min(x for x in (first_copy, retirement, cutoff) if x is not None)
                history = changes[(zone, generation)]
                ids = [c['事件'] for c in history]
                pos = bisect_right(ids, seq)-1
                require(pos >= 0, '缺少初始映射组成')
                candidates = [dict(history[pos], 事件=seq, 单调纳秒=ns)] + [
                    c for c in history[pos+1:] if seq < c['事件'] < last]
                join = next((c for c in candidates if c['组成']['已知H字节'] > 0 and
                             c['组成']['H状态未确认'] == 0), None)
                target = max((t for t in report['组成']['目标'] if t['类型'] == '实际目标'
                              and t['对象'] == own and t['Zone'] == zone and
                              t['代际'] == generation and t['事件'] < seq),
                             key=lambda t: t['事件'])
                require(target['路径'] == '新文件', '原始片段来源不是新文件写入')
                pool = any(t['类型'] == '池目标' and t['对象'] == own and
                           t['Zone'] == zone and t['代际'] == generation and
                           t['路径'] == '新文件' and t['事件'] < target['事件']
                           for t in report['组成']['目标'])
                h_arrival = None
                if join and join['事件'] > seq:
                    # H复制成功不等于映射已提交；同时核对后面的MapEnd。
                    for c in selected:
                        if c['当前类别'] not in ('H_only', 'HW') or (
                            int(c['目标Zone']), int(c['目标代际'])) != (zone, generation):
                            continue
                        cp = int(c['组成事件'])
                        if not seq < cp <= join['事件']:
                            continue
                        h_owner = at[cp][5]
                        commit = next((r[0] for r in events[cp:] if r[5] == h_owner and r[3] == 6), None)
                        if commit == join['事件']:
                            h_arrival = dict(文件编号=int(c['文件编号']), 复制事件=cp,
                                             映射事件=commit, 原生复制行=int(c['原生复制行']))
                record = dict(模式=mode, 文件编号=fid, 对象=own, 初始片段序号=index,
                    初始Zone编号=zone//ZONE, 代际=generation, 片段起点=start,
                    有效字节=length, 初始入账事件=seq, 新文件目标事件=target['事件'],
                    原新文件取得池目标=pool, 写入前目标H字节=target['当前组成']['已知H字节'],
                    首次共存事件=join['事件'] if join else None,
                    首次共存路径=('新Flush写入已有H区' if join and join['事件'] == seq else
                                  'H必要迁移进入已有W区' if h_arrival else
                                  '其他状态变化，证据另列' if join else '原始落点未观察到H共存'),
                    H到达复制事件=h_arrival['复制事件'] if h_arrival else None,
                    H到达映射事件=h_arrival['映射事件'] if h_arrival else None,
                    共存时H字节=join['组成']['已知H字节'] if join else None,
                    共存时W字节=join['组成']['已知Flush字节'] if join else None,
                    共存时W年龄秒=(join['单调纳秒']-first[1])/1e9 if join else None,
                    首次搬迁事件=first_copy, 首次搬迁类型=chain[0]['维护类型'] if chain else None,
                    共存到首次复制秒=(at[first_copy][1]-join['单调纳秒'])/1e9 if join and first_copy else None,
                    原生首次复制行=int(chain[0]['原生复制行']) if chain else None,
                    本片段复制次数=len(chain), 本片段总复制字节=sum(int(c['有效复制字节']) for c in chain),
                    逻辑退役事件=retirement, 实际释放事件=release,
                    入账到退役秒=(at[retirement][1]-first[1])/1e9 if retirement else None,
                    入账到截尾秒=(at[cutoff][1]-first[1])/1e9,
                    边界='年龄自首次有效extent入账，不是Key寿命；复制时刻不是RR触发时刻；末尾析构Clear不算业务退役')
                result.append(record)
                mode_records.append(dict(片段=record, H到达证据=h_arrival, 全部实际复制=chain))
        timelines[mode] = mode_records
    output.mkdir(parents=True)
    with (output/'首次混放逐片段.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, list(result[0]));writer.writeheader();writer.writerows(result)
    (output/'完整路径与证据.json').write_text(json.dumps(dict(原始配对=str(pair),
        原始SHA256=evidence, 各臂=timelines), ensure_ascii=False, indent=2)+'\n')
    require(len(result) == 10, '本轮三文件初始片段数量与预期不同')
    # 用独立原生复制表核对每个原始片段链，不能因未找到关联而把成本记成零。
    for mode in MODES:
        for fid in FILES:
            reconstructed = sum(r['本片段总复制字节'] for r in result
                                if r['模式'] == mode and r['文件编号'] == fid)
            native = sum(int(c['有效复制字节']) for c in copies
                         if c['模式'] == mode and int(c['文件编号']) == fid)
            require(reconstructed == native, '首次片段链与文件全部原生复制不闭合')
    (output/'离线核验.json').write_text(json.dumps(dict(通过=True, 初始片段记录数=len(result),
        六条逐文件复制量均闭合=True, 原始来源哈希与上一阶段一致=True,
        H后到路径已核对复制与映射结束=True, 新业务实验=False,
        说明='本次时间从同一trace的steady_clock取差；不混用墙钟，不把析构清空视为业务失效'),
        ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pair', type=Path)
    parser.add_argument('previous', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    results = analyze(args.pair, args.previous, args.output)
    print(json.dumps(results, ensure_ascii=False, indent=2))
