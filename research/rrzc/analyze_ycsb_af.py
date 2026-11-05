"""独立核验本轮32GiB标准语义A至F；只接受完整真实运行，不要求收益为正。"""
import json
from pathlib import Path
import re

from ycsb_af import require, digest, properties, check_trace, latencies, LABELS
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_joint_opportunity import fields
from analyze_deadline import audit as space_audit, audit_forecasts
from analyze_terminal import audit as terminal_audit
from analyze_window_credit import audit_scores
from client_phase import phases
from ycsb_throughput import throughput
from rrzc_rr_health import inspect


def analyze_arm(root, tag, letter, mode, records, ops):
    root = Path(root)
    require(mode in LABELS, '未登记策略')
    for name in ('wrapper.exit', 'business.exit', 'verify.exit', 'runner.exit'):
        require((root/name).read_text().strip() == '0', '运行失败：' + name)
    prefix = Path('/home/femu/rrzc-sst-results')/tag
    archived = set()
    for line in (root/'logs.sha256').read_text().splitlines():
        value, name = line.split(maxsplit=1)
        relative = Path(name).relative_to(prefix)
        require('..' not in relative.parts and relative not in archived and
                digest(root/relative) == value, '原始日志哈希或路径错误：' + name)
        archived.add(relative)
    require({Path(n) for n in ('ycsb.log', 'zenfs.run.log', 'audit.requests', 'audit.expected',
             'strict.business.json', 'strict.after-verify.json', 'zones.final.json')} <= archived,
            '关键证据未进入归档')
    before = (root/'software.before.log').read_text()
    require(before == (root/'software.after.log').read_text() and before and
            all(row.endswith(': OK') for row in before.splitlines()), '软件身份改变')
    props = properties((root/'workload.properties').read_text())
    require(props['recordcount'] == str(records) and props['operationcount'] == str(ops) and
            props['rrzc.independent_keys'] == 'false' and props['limit.ops'] == '0' and
            props['rocksdb.disable_wal'] == props['rocksdb.no_block_cache'] == 'true' and
            props['rocksdb.fs_uri'] == 'zenfs://dev:nvme0n1', '负载或数据库口径不符')
    trace = check_trace(root/'audit.requests', records, ops, letter)
    count = {k: int(v) for k, v in properties((root/'audit.counts').read_text()).items()}
    require(count == dict(版本=2, 装载=records, 读取=trace['读取'], 更新=trace['更新'],
            插入=trace['插入'], 扫描=trace['扫描'], 读改写=trace['读改写'], 键数=trace['最终键数']),
            '逻辑请求与API审计计数不符')
    require(f'只读全键最新值哈希校验通过，键数={trace["最终键数"]}' in
            (root/'verify.log').read_text(), '独立全键校验未完成')
    ycsb = (root/'ycsb.log').read_text()
    require(ycsb.count('实验WAL配置：关闭=1') == 1, '关闭WAL未确认')
    zen = (root/'zenfs.run.log').read_text()
    lines = zen.splitlines()
    enabled = mode == 'model'
    for marker in ('严格RR主机配置：启用=1，', '公共内容隔离配置：启用=1，',
                   '统一选区配置：启用=1，', '时间一致配置：版本=1，启用=1，',
                   '边界配置：版本=2，启用=' + str(int(enabled)) + '，'):
        require(zen.count(marker) == 1, '实际配置缺失或重复：' + marker)
    require(('窗口收益配置：' in zen) == enabled and '支持预测配置：' not in zen,
            '模型版本或开关不符')
    levels = set(re.findall(r'GC 检查：.*启动水平=(\d+)%', zen))
    require(not levels or levels == {'20'}, 'GC阈值改变')
    rock = (root/'aux/rrzc-ycsb/LOG').read_text()
    require('[ERROR]' not in zen and '状态=失败' not in zen and not re.search(
            r'Background (compaction|flush) error|Zone allocation failure|Corruption:', rock),
            '实际维护或数据库后台错误')
    plans = terminal_audit(lines, version=2) if enabled else space_audit(lines)
    forecasts = audit_forecasts(lines, time_consistent=True)
    scores = audit_scores(lines, 'window' if enabled else 'greedy', persistent=enabled,
            expected_rr=enabled, candidate_pool='expanded', space_plan=True, deadline=True,
            joint_time=True, terminal=enabled, terminal_version=2) if '统一成本：' in zen else []
    rr = [tuple(map(int, m.groups())) for line in lines if (m := RR_STATS.search(line))]
    zc = [tuple(map(int, m.groups())) for m in ZC_STATS.finditer(zen)]
    require(all(row[2] == 1 for row in rr + zc), '非单区维护')
    copies = [fields(s, 'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved = sum(row[1] for row in rr + zc)
    require(all(row['复制成功'] == '1' for row in copies) and
            sum(int(row['有效字节']) for row in copies) == moved and
            zen.count('RR 开始：') == zen.count('RR 完成：') == len(rr) and
            zen.count('RR公平队列服务：') == len(rr), '复制或RR维护账目不闭合')
    devices = [json.loads((root/f'device.{side}.json').read_text()) for side in ('initial', 'before', 'after')]
    require(all(len(d['Zone']) == 256 and d['每区块数'] == 128 and d['RR阈值'] == 1024
                for d in devices), '32GiB设备几何不符')
    require(len({(d['会话高'], d['会话低']) for d in devices}) == 1 and
            all(z['代际'] == z['RC'] == 0 for z in devices[0]['Zone']), '非新建NAND或设备会话改变')
    initial = json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(initial) == 256 and all(z['state'] == 'EMPTY' and z['wp'] == z['slba'] and
            z['cap']*512 == 128 << 20 for z in initial), '初态并非全空32GiB盘')
    first, last = [{z['地址']: z['代际'] for z in d['Zone']} for d in devices[1:]]
    require(first.keys() == last.keys() and all(last[z] >= first[z] for z in first), 'Reset代际回退')
    resets = sum(last[z]-first[z] for z in first)
    require(resets == len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功，', zen)) and
            resets >= len(rr)+len(zc), '成功Reset账目不闭合')
    stats = [json.loads((root/n).read_text()) for n in ('strict.business.json', 'strict.after-verify.json')]
    for row in stats:
        require(row['版本'] == row['启用'] == 1 and 0 <= row['历史最高RC'] <= 1024 and
                row['实际NAND读取'] == row['计入RC读取']+row['自身刷新豁免读取'] and
                row['其他块搬迁计数读取'] <= row['计入RC读取'], '严格RR计数错误或越阈值')
    require(all(stats[1][k] >= stats[0][k] for k in stats[0]), '校验后计数回退')
    health = inspect(zen)
    # 少于一个检查周期的功能小测可能没有风险帧；业务安全另由严格计数核验。
    require(health['验收通过'] or (tag.startswith('smoke-') and
            health['错误'] == ['缺少完整RR风险帧']), 'RR健康未通过：' + str(health['错误']))
    timing = phases(ycsb, ops)
    require(timing['可知'] and timing['前台请求秒'] > 0, '客户端阶段计时缺失')
    zones = json.loads((root/'zones.final.json').read_text())['zone_list']
    return dict(验收通过=True, 标签=tag, 工作负载=letter.upper(), 策略=mode,
                策略名称=LABELS[mode], 真实请求=trace, API调用延迟=latencies(ycsb, trace),
                吞吐量=throughput(ycsb, ops, props), 客户端阶段耗时=timing,
                前台吞吐ops每秒=ops/timing['前台请求秒'], RR次数=len(rr), ZC次数=len(zc),
                总迁移字节=moved, RR迁移字节=sum(r[1] for r in rr), ZC迁移字节=sum(r[1] for r in zc),
                总成功ZoneReset=resets, 推导物理Block擦除次数=resets*128,
                请求SHA256=digest(root/'audit.requests'), 最终值SHA256=digest(root/'audit.expected'),
                负载SHA256=digest(root/'workload.properties'), RR健康=health, 严格RR业务账目=stats[0],
                正RR估计数=sum(p['RR抵扣'] > 0 for p in forecasts), 实际选区轮数=len(plans),
                评分核验记录数=len(scores),
                RR独立改选次数=sum(p.get('实际Zone') != p.get('RR项置零Zone') for p in plans) if enabled else None,
                末态硬件可写字节=sum(max(0, z['slba']+z['cap']-z['wp'])*512 for z in zones if z['state'] != 'FULL'),
                口径='32GiB未锁页；维护含装载、Run与Cleanup，不含独立校验；API非裸IO；F的RMW为含生成与审计的完整事务延迟；擦除为Reset乘128推导；单轮非稳态结论')


def compare_pair(output, stage, letter, records, ops):
    output = Path(output)
    arms = {}
    for mode in LABELS:
        tag = f'{stage}-{letter}-{mode}'
        archive = json.loads((output/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'], '归档或关机未完成')
        arms[mode] = analyze_arm(output/tag/tag, tag, letter, mode, records, ops)
    for key in ('请求SHA256', '最终值SHA256', '负载SHA256'):
        require(arms['space'][key] == arms['model'][key], '同负载配对不一致：' + key)
    folders = [output/f'{stage}-{letter}-{m}'/f'{stage}-{letter}-{m}' for m in LABELS]
    require((folders[0]/'software.before.log').read_bytes() ==
            (folders[1]/'software.before.log').read_bytes(), '两组不是同一库及客户端')
    return dict(配对验收通过=True, 阶段=stage, 工作负载=letter.upper(), 模式=arms,
                差值={k: arms['model'][k]-arms['space'][k] for k in
                      ('RR次数', 'ZC次数', '总迁移字节', '总成功ZoneReset', '前台吞吐ops每秒')})
