"""核对真实SST退役与每笔迁移分配回退，不改变维护或放置策略。"""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from analyze_sst_compaction import events
from pool_dbbench_guest import key, table
from ycsb_af import digest, require

ZONE = 128 << 20


def original_qualification(valid, capacity, threshold):
    """精确复现冻结代码的整数近似与严格大于，不能改成真实浮点无效比例。"""
    require(capacity > 0 and 0 <= valid <= capacity and 0 <= threshold <= 100, '资格输入越界')
    garbage = 100 - 100 * valid // capacity
    return dict(无效比例整数近似=garbage, 资格=valid > 0 and garbage > threshold)


def allocation_evidence(lines, task, labels):
    """只关联同维护线程、当前文件/片段的分配证据，不借用别的线程或前文件。"""
    thread = task['原始选择行'].split()[1]; result = []; previous = task['开始行'] - 1
    for item in task['复制']:
        row = item['字段']; end = item['行'] - 1
        relevant = [(i+1, l) for i, l in enumerate(lines[previous-1:end-1], previous-1)
                    if len(l.split()) > 1 and l.split()[1] == thread]
        begins = [(n,l) for n,l in relevant if 'MigrateFileExtents, fname: ' + row['文件名'] + ',' in l]
        if begins: relevant = [(n,l) for n,l in relevant if n >= begins[-1][0]]
        decisions = [(n,l) for n,l in relevant if '协同池取得目标：' in l or '协同池回退：' in l]
        expected = labels.get(row['文件名'], '普通')
        if expected in ('H', 'W', 'HW'):
            require(len(decisions) == 1, '标签迁移缺少唯一分配结论：' + row['文件名'])
            n, line = decisions[0]
            accepted = '协同池取得目标：' in line
            decision = fields(line, '协同池取得目标：' if accepted else '协同池回退：')
            if accepted:
                require(int(decision['Zone']) == int(row['目标Zone']), '协同分配目标与实际复制不符')
            mode = '协同目标' if accepted else '普通回退'
            reason = decision['原因']
        else:
            require(not decisions, '无标签文件却使用了上一文件的池诊断')
            n, line, mode, reason = None, None, '未分类普通分配', '没有受控H/W标签'
        result.append(dict(类型=task['类型'], 文件=row['文件名'], 标签=expected,
            源Zone=int(row['源Zone'])//ZONE, 目标Zone=int(row['目标Zone'])//ZONE,
            字节=int(row['有效字节']), 目标写入前缀字节=int(row['目标片段'])-int(row['目标Zone']),
            分配路径=mode, 原因=reason, 复制行=end, 分配行=n, 原始分配=line))
        previous = end + 1
    return result


def arm(root, verified, labels):
    original = {r['文件']:r for r in table(root/'布局-0.sst.csv')}
    final = {r['文件']:r for r in table(root/'布局-61.sst.csv')}
    w = {n for n in original if labels.get('/rrzc-dbbench'+n)=='W'}
    require(len(w)==30, 'W0预注册范围不同')
    records = events(root/'run.rocksdb.log')
    starts = [r for r in records if r['记录'].get('event')=='compaction_started']
    finishes = [r for r in records if r['记录'].get('event')=='compaction_finished']
    require(Counter(r['记录']['job'] for r in starts)==Counter(r['记录']['job'] for r in finishes), 'Compaction未闭合')
    l3 = {n for r in starts for n in r['记录'].get('files_L3', [])}
    deleted = {r['记录']['file_number']:r for r in records
               if r['记录'].get('event')=='table_file_deletion' and r['记录'].get('status')=='OK'}
    details = []
    for name in sorted(w):
        old = original[name]; number = int(name[1:-4]); low = key(old['最小键十六进制']); high = key(old['最大键十六进制'])
        merged = [r for r in starts if number in r['记录'].get('files_L3', [])]
        newer = [dict(文件=n, 层级=int(r['层级']), 最小键=key(r['最小键十六进制']), 最大键=key(r['最大键十六进制']))
                 for n,r in final.items() if n not in original and int(r['层级'])<int(old['层级'])
                 and key(r['最小键十六进制'])<=high and key(r['最大键十六进制'])>=low]
        live = name in final
        require(int(old['层级'])==3, 'W0初始层级改变')
        require((not live and number in l3 and number in deleted) or
                (live and number not in l3 and number not in deleted and newer), '旧文件退役/存活证据未闭合')
        details.append(dict(文件=name, 初始层级=3, 最小键=low, 最大键=high, 末端仍存活=live,
            L3合并任务=merged, 删除事件=deleted.get(number), 上层同键新文件=newer))
    text = (root/'run.rocksdb.log').read_text()
    def option(name):
        matches = re.findall(r'Options\.'+re.escape(name)+r': ([^\s]+)', text)
        require(len(matches)==1, '配置项不唯一：'+name); return matches[0]
    base = int(option('max_bytes_for_level_base')); multiplier = float(option('max_bytes_for_level_multiplier'))
    require(option('level_compaction_dynamic_level_bytes')=='0', '静态分层条件不同')
    target = int(base*multiplier)
    levels = defaultdict(lambda: dict(文件数=0, 字节=0))
    for r in final.values(): levels[r['层级']]['文件数']+=1; levels[r['层级']]['字节']+=int(r['字节'])
    lifetime = []
    for n in range(62):
        live = {r['文件'] for r in table(root/f'布局-{n}.sst.csv')}
        lifetime.append(dict(布局=n, 原W存活=len(w & live)))
    raw = root/'run.zenfs.log'; lines = raw.read_text().splitlines()
    copies = []
    for task in verified['事务']: copies.extend(allocation_evidence(lines, task, labels))
    require(sum(c['字节'] for c in copies)==verified['维护']['总迁移字节'], '分配关联没有覆盖完整迁移字节')
    h = [r for r in copies if r['标签']=='H']
    reasons = Counter(r['原因'] for r in h)
    require(len(h)==14, '本轮H两文件七次RR链改变')
    flush_jobs = {r['记录']['job'] for r in records if r['记录'].get('event')=='flush_started'}
    flush_files = [r for r in records if r['记录'].get('event')=='table_file_creation'
                   and r['记录']['job'] in flush_jobs and r['记录'].get('status')=='OK']
    flush_lifetimes = []
    for r in flush_files:
        number = r['记录']['file_number']; dead = deleted.get(number)
        span = (dead['记录']['time_micros']-r['记录']['time_micros'])/1e6 if dead else None
        require(span is None or span >= 0, '文件退役时间早于创建')
        flush_lifetimes.append(dict(文件编号=number, 创建事件=r, 删除事件=dead, 观测寿命秒=span,
                                   业务截止仍未观测删除=dead is None))
    import statistics
    measured = [r['观测寿命秒'] for r in flush_lifetimes if r['观测寿命秒'] is not None]
    hot_bytes = sum(int(r['字节']) for n,r in original.items() if labels.get('/rrzc-dbbench'+n)=='H')
    return dict(W0文件=details, W0存活轨迹=lifetime, 初次退役布局=next(r['布局'] for r in lifetime if r['原W存活']<30),
        末端W存活=sum(d['末端仍存活'] for d in details), 闭合Compaction数=len(starts),
        含L3旧文件合并数=sum(bool(r['记录'].get('files_L3')) for r in starts),
        第一笔L3合并=next(r for r in starts if r['记录'].get('files_L3')),
        L2静态目标字节=target, 末端分层=dict(levels), L2末端目标比=levels['2']['字节']/target,
        原分层参数=dict(基础字节=base, 倍率=multiplier, 动态分层=False, 合并优先级=option('compaction_pri')),
        全部迁移分配=copies, H迁移分配=h, H分配原因=dict(reasons),
        Flush新文件寿命=flush_lifetimes,
        Flush寿命汇总=dict(创建数=len(flush_files), 已观测删除数=len(measured), 截止未观测删除数=len(flush_files)-len(measured),
            已删除样本中位秒=statistics.median(measured), 已删除样本最长秒=max(measured),
            十秒内已删除数=sum(v<=10 for v in measured), 十四秒内已删除数=sum(v<=14 for v in measured)),
        仅H仍存活时资格=dict(H字节=hot_bytes, 容量字节=ZONE,
            各门槛={str(t):original_qualification(hot_bytes,ZONE,t) for t in (79,85,88)}),
        证据哈希={p:digest(root/p) for p in ('run.rocksdb.log','run.zenfs.log','布局-0.sst.csv','布局-61.sst.csv')})


def analyze(source, output):
    state = json.loads((source/'状态.json').read_text()); archive = json.loads((source/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None,
            '来源未完整归档退出')
    require(json.loads((source/'Greedy重复核验.json').read_text())['通过'], 'Greedy未重复通过')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'], '完整归档不同')
    labels = json.loads((source/'源码/受控标签来源.json').read_text())['标签']
    reports = {t:arm(source/'逐臂采证'/t, json.loads((source/(t+'-核验.json')).read_text()), labels) for t in ('p1','p2')}
    require(reports['p1']['H分配原因']==reports['p2']['H分配原因'], '分配原因跨臂不同，须定位而不能合并报告')
    require(not output.exists(), '不覆盖派生结果'); output.mkdir(parents=True)
    for tag,r in reports.items():
        with (output/(tag+'-全部迁移分配.csv')).open('x', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(r['全部迁移分配'][0])); w.writeheader(); w.writerows(r['全部迁移分配'])
    result = dict(来源=str(source), 完整归档SHA256=state['镜像处理']['archive_sha256'], 各臂=reports,
        分析源码SHA256=digest(Path(__file__)),
        边界='层容量是按配置和元数据计算，不代替Picker内部全候选评分；迁移回退仅依据实际分配日志，不推测当时所有空区/令牌内部状态。')
    (output/'文件寿命与分配根因.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('source',type=Path); p.add_argument('output',type=Path)
    args=p.parse_args(); x=analyze(args.source,args.output)
    print(json.dumps({t:{k:r[k] for k in ('初次退役布局','末端W存活','闭合Compaction数',
                        '含L3旧文件合并数','L2末端目标比','H分配原因')} for t,r in x['各臂'].items()},ensure_ascii=False,indent=2))
