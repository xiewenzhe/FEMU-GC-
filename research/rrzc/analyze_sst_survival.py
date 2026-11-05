"""只读验证真实SST近期退役信号；未来删除只作标签，不进入当时特征。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from analyze_v1_shadow_stream import fields, require

SECOND = 1_000_000
WINDOW = 10 * SECOND
AGE_BOUNDS = (10, 30, 120)


def stamp(line):
    """只作同一客机日志的时钟对齐；不转换成宿主本地时间。"""
    value = datetime.strptime(line[:26], '%Y/%m/%d-%H:%M:%S.%f')
    return int(value.replace(tzinfo=timezone.utc).timestamp()) * SECOND + value.microsecond


def age_group(age):
    return next((f'小于{b}秒' for b in AGE_BOUNDS if age < b), '至少120秒')


def parse_rocks(text):
    """限定单次空库会话；记录事件可见时间，不提前使用延迟输出的事件。"""
    require(text.count('RocksDB version:') == 1, '需要单次数据库会话，不能拼接文件编号')
    require('SST files in /rrzc-ycsb dir, Total Num: 0, files:' in text,
            '未确认从空库开始，无法假定生命周期覆盖完整')
    require('Shutdown complete' in text, '缺正常结束证据')
    files, jobs, counts = {}, {}, Counter()
    last = 0
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r'\d{4}/\d\d/\d\d-', line):
            last = max(last, stamp(line))
        if 'EVENT_LOG_v1 ' not in line:
            continue
        event = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
        when = max(stamp(line), int(event['time_micros']))
        kind = event['event']
        counts[kind] += 1
        if kind == 'table_file_creation':
            require(event.get('cf_name') == 'default', '存在未支持的列族')
            success = event.get('status') == 'OK'
            require(success or (event.get('status', '').startswith('Shutdown in progress:')
                                and int(event['file_size']) == 0),
                    '存在非正常关库取消的创建失败')
            fid = int(event['file_number'])
            require(fid not in files, '文件编号重复，不能跨会话拼接')
            files[fid] = dict(创建可见微秒=when, 创建行号=number, 创建成功=success,
                              文件大小=int(event['file_size']), Compaction=[])
        elif kind == 'compaction_started':
            job = int(event['job'])
            require(job not in jobs, 'Compaction任务重复')
            inputs = [int(fid) for key, values in event.items()
                      if re.fullmatch(r'files_L\d+', key) for fid in values]
            require(inputs and len(inputs) == len(set(inputs)), 'Compaction输入缺失或重复')
            jobs[job] = dict(开始微秒=when, 开始行号=number, 输入=inputs,
                             结束微秒=None, 结束行号=None)
        elif kind == 'compaction_finished':
            job = jobs[int(event['job'])]
            require(job['结束微秒'] is None and when >= job['开始微秒'], 'Compaction完成顺序错误')
            job.update(结束微秒=when, 结束行号=number)
        elif kind == 'table_file_deletion':
            require(event.get('status') == 'OK', '存在未成功的文件删除')
            file = files[int(event['file_number'])]
            require('删除可见微秒' not in file, '文件重复删除')
            file.update(删除可见微秒=when, 删除行号=number)
    for jid, job in jobs.items():
        require(job['结束微秒'] is not None, '存在未结束Compaction，不能默认为成功')
        for fid in job['输入']:
            require(fid in files, '缺Compaction输入文件的创建事件')
            files[fid]['Compaction'].append(dict(任务=jid, **job))
    return files, last, dict(counts)


def parse_zenfs(text):
    retirements, blocks, costs, candidates = {}, {}, {}, []
    last = 0
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r'\d{4}/\d\d/\d\d-', line):
            last = max(last, stamp(line))
        if 'V1后续文件退役：' in line:
            r = fields(line, 'V1后续文件退役：')
            match = re.search(r'文件名=/rrzc-ycsb/(\d+)\.sst(?:，|$)', line)
            if match:
                require('最后链接已删除，元数据持久化成功' in line, '退役未持久化')
                fid = int(match[1])
                require(fid not in retirements, 'SST重复退役')
                retirements[fid] = dict(退役微秒=stamp(line), 退役行号=number,
                                       ZenFS编号=r['文件编号'])
        for marker, target in (('V1影子候选：', blocks), ('统一成本：', costs)):
            if marker in line:
                r = fields(line, marker)
                key = r['轮次'], r['Zone']
                require(key not in target, '同轮候选重复')
                target[key] = dict(r, 观测微秒=stamp(line), 行号=number)
        if 'V1影子文件：' in line:
            r = fields(line, 'V1影子文件：')
            match = re.fullmatch(r'/rrzc-ycsb/(\d+)\.sst', r['文件名'])
            if match:
                candidates.append(dict(r, SST编号=int(match[1]), 文件行号=number))
    for r in candidates:
        key = r['轮次'], r['Zone']
        require(key in blocks, '缺候选开始记录')
        require(blocks[key].get('原候选资格') in ('0', '1'), '缺原空间候选资格')
        r.update(观测微秒=blocks[key]['观测微秒'], 候选行号=blocks[key]['行号'],
                 原候选资格=blocks[key]['原候选资格'])
        cost = costs.get(key)
        r['ETA纳秒'] = int(cost['ETA']) if cost and cost.get('风险可知') == '1' else None
        r['RR抵扣字节'] = int(cost['RR抵扣']) if cost else 0
    return retirements, candidates, last


def features(file, when):
    """只读截止此刻可见的创建与任务开始/完成；不使用未来持续时长。"""
    require(file['创建可见微秒'] < when, '候选早于创建可见时间，年龄未知')
    age = (when - file['创建可见微秒']) / SECOND
    active = [j['任务'] for j in file['Compaction']
              if j['开始微秒'] < when <= j['结束微秒']]
    return dict(创建事件龄秒=age, 年龄分组=age_group(age),
                已进入进行中Compaction=bool(active), 可见任务=active)


def label(file, when, horizon, end):
    """返回十秒内是否退役；窗口不足时右删失，不能把存活到结束当作负例。"""
    death = file.get('退役微秒')
    if death is not None and death <= when:
        return None
    if death is not None and when < death <= min(when + horizon, end):
        return 1
    if when + horizon <= end:
        return 0
    return None


def evaluate(samples, cut, end):
    """固定时间二分、每文件首次观察；验证集剔除训练已见文件，不调参。"""
    train, test, seen = [], [], set()
    for r in sorted(samples, key=lambda r: (r['观测微秒'], r['SST编号'])):
        if r['观测微秒'] < cut:
            if r['SST编号'] in seen:
                continue
            seen.add(r['SST编号'])
            train.append(dict(r, 标签=label(r, r['观测微秒'], WINDOW, cut)))
    used = set(seen)
    for r in sorted(samples, key=lambda r: (r['观测微秒'], r['SST编号'])):
        if r['观测微秒'] < cut or r['SST编号'] in used:
            continue
        used.add(r['SST编号'])
        test.append(dict(r, 标签=label(r, r['观测微秒'], WINDOW, end)))
    # 训练和计分都要求完整固定窗口，避免末尾只纳入已死亡、排除未知存活者。
    known = [r for r in train if r['观测微秒'] + WINDOW <= cut and r['标签'] is not None]
    require(known, '训练期无可核验标签')
    base = sum(r['标签'] for r in known) / len(known)
    ages, active = defaultdict(list), defaultdict(list)
    for r in known:
        ages[r['年龄分组']].append(r['标签'])
        active[r['已进入进行中Compaction']].append(r['标签'])

    def mean_or_base(group):
        return sum(group) / len(group) if group else base

    rows = []
    for r in test:
        probabilities = {'全部存活': 0.0, '训练期总体': base,
                         '创建事件龄': mean_or_base(ages[r['年龄分组']]),
                         '进行中Compaction': mean_or_base(active[r['已进入进行中Compaction']])}
        rows.append(dict(r, 预测十秒退役概率=probabilities))
    scored = [r for r in rows if r['观测微秒'] + WINDOW <= end and r['标签'] is not None]
    scores = {}
    for key in ('全部存活', '训练期总体', '创建事件龄', '进行中Compaction'):
        errors = [((r['预测十秒退役概率'][key] - r['标签']) ** 2,
                   int(r['区内有效字节'])) for r in scored]
        scores[key] = dict(样本数=len(errors),
            Brier=sum(e for e, _ in errors)/len(errors) if errors else None,
            字节加权Brier=sum(e*w for e, w in errors)/sum(w for _, w in errors) if errors else None)

    def groups(data, key):
        grouped = defaultdict(list)
        for r in data:
            grouped[str(r[key])].append(r)
        return {name: dict(样本数=len(group), 未知数=sum(r['标签'] is None for r in group),
            已知标签数=sum(r['标签'] is not None for r in group),
            十秒退役数=sum(r['标签'] == 1 for r in group)) for name, group in grouped.items()}

    return dict(训练首次文件数=len(train), 训练完整窗口数=len(known),
        验证首次新文件数=len(test), 验证完整窗口数=len(scored),
        验证右删失数=sum(r['标签'] is None for r in rows),
        验证不足完整窗口数=len(test)-len(scored),
        训练完整窗口年龄分组=groups(known, '年龄分组'),
        验证完整窗口年龄分组=groups(scored, '年龄分组'),
        训练完整窗口Compaction分组=groups(known, '已进入进行中Compaction'),
        验证完整窗口Compaction分组=groups(scored, '已进入进行中Compaction'),
        误差=scores, 验证明细=rows)


def analyze(rocks_text, zenfs_text):
    files, rock_end, counts = parse_rocks(rocks_text)
    retired, candidates, zen_end = parse_zenfs(zenfs_text)
    deleted = {fid for fid, file in files.items() if '删除可见微秒' in file}
    require(deleted == set(retired), 'RocksDB删除与ZenFS持久化退役不一一对应')
    for fid, row in retired.items():
        file = files[fid]
        require(file['创建可见微秒'] <= row['退役微秒'] <= file['删除可见微秒'],
                '文件创建、持久化退役、删除成功顺序不符')
        file.update(row)
    end = min(rock_end, zen_end)
    samples, races = [], []
    for c in candidates:
        fid, when = c['SST编号'], c['观测微秒']
        require(fid in files, '候选文件缺创建事件')
        file = files[fid]
        require(file['创建成功'], '未成功创建的SST不能作为有效候选')
        if when >= end or file.get('退役微秒', end+1) <= when:
            races.append(c)
            continue
        # 从文件名关联两个编号空间；不假定ZenFS编号等于RocksDB编号。
        require('ZenFS编号' not in file or file['ZenFS编号'] == c['文件编号'], '文件名关联编号不一致')
        # 未来事件明细不混入候选特征；退役时刻仅由label和事后核验使用。
        record = {k: v for k, v in file.items() if k != 'Compaction'}
        samples.append(dict(c, **record, **features(file, when),
                            十秒退役标签=label(file, when, WINDOW, end)))
    require(samples, '无可用候选SST')
    eligible = [r for r in samples if r['原候选资格'] == '1']
    require(eligible, '无符合原空间资格的候选SST')
    cut = (min(r['观测微秒'] for r in eligible) + max(r['观测微秒'] for r in eligible)) // 2
    evaluation = evaluate(eligible, cut, end)
    urgent = []
    grouped = defaultdict(list)
    for r in samples:
        if r['RR抵扣字节'] > 0 and r['ETA纳秒'] is not None and r['ETA纳秒'] <= 10_000_000_000:
            grouped[r['轮次'], r['Zone']].append(r)
    for (rid, zone), group in grouped.items():
        outcomes = [dict(SST编号=r['SST编号'], 文件编号=r['文件编号'],
            区内有效字节=int(r['区内有效字节']), 创建事件龄秒=r['创建事件龄秒'],
            已进入进行中Compaction=r['已进入进行中Compaction'], 可见任务=r['可见任务'],
            ETA前退役标签=label(r, r['观测微秒'], (r['ETA纳秒']+999)//1000, end),
            后续退役秒=(r['退役微秒']-r['观测微秒'])/SECOND if '退役微秒' in r else None)
            for r in group]
        urgent.append(dict(轮次=rid, Zone=zone, ETA秒=group[0]['ETA纳秒']/1e9,
                           原预计RR抵扣字节=group[0]['RR抵扣字节'], 文件=outcomes))
    return dict(核验通过=True, SST创建数=len(files),
        SST成功创建数=sum(f['创建成功'] for f in files.values()),
        关库取消空文件数=sum(not f['创建成功'] for f in files.values()),
        SST持久化退役数=len(retired),
        末尾未退役文件数=len(files)-len(retired), 事件计数=counts,
        候选文件记录数=len(candidates), 可分析记录数=len(samples),
        原资格候选文件记录数=len(eligible),
        快照退役竞态或超出共同观察期记录=races,
        时间切分微秒=cut, 共同结束微秒=end, 预测窗口秒=10,
        年龄分组边界秒=list(AGE_BOUNDS),
        时间切分方法='原合格候选首次和末次观察的时间中点；各臂独立训练，互不合并',
        时间隔离验证=evaluation, 有RR抵扣候选=urgent,
        边界='单轮旧8GiB/RR4096 YCSB的离线观察，不是新性能结果或FineRR32GiB实验。'
        '创建事件龄不是ZenFS修改龄；寿命提示和完整当前层级未采集，不猜测。'
        '预测评估仅用原空间资格合格候选；不额外声称全部满足执行前目的区安全复核。'
        '每文件每阶段首次合格候选观察，验证排除训练已见文件；训练末尾窗口不足标未知。'
        '标签是原运行中的物理文件退役，不是键值消失，也不是已证明避免RR。'
        'Compaction状态只用当时已输出的开始/完成事件，时长不作特征；尚未接入在线ZenFS。'
        '不足完整窗口的样本保留，但训练和Brier均排除，包括末尾已观测到退役者；'
        '不以删失样本当成存活负例。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='含greedy/direct的原始归档目录')
    parser.add_argument('--output', type=Path, required=True, help='新建派生JSON，不覆盖已有结果')
    args = parser.parse_args()
    require(not args.output.exists(), '派生结果已存在，拒绝覆盖')
    output = {}
    for arm in ('greedy', 'direct'):
        root = args.root / arm / '20261017'
        paths = [root/'aux/rrzc-ycsb/LOG', root/'zenfs.run.log']
        raw = [path.read_bytes() for path in paths]
        output[arm] = analyze(*(data.decode() for data in raw))
        output[arm]['分析器SHA256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        output[arm]['原始文件'] = [dict(路径=str(p.resolve()), SHA256=hashlib.sha256(b).hexdigest())
                                    for p, b in zip(paths, raw)]
    with args.output.open('x') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({arm: {k: v for k, v in result.items() if k in
          ('核验通过', 'SST创建数', 'SST持久化退役数', '末尾未退役文件数', '候选文件记录数')}
          for arm, result in output.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
