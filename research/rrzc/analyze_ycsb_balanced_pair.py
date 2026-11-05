"""只比较同库同请求的两组真实归档；允许零改选和负收益。"""
import json
from pathlib import Path
from analyze_ycsb_retention import sha
from ycsb_throughput import throughput
from analyze_v1_shadow_stream import require

MODES = ('greedy', 'retention')


def compare_results(arms):
    require(set(arms) == set(MODES), '缺少配对组或混入其他组')
    for mode, row in arms.items():
        require(row['验收通过'] and row['模式'] == mode, '存在无效组或策略不符')
        require(row['计划业务操作数'] == 1200000 and row['无WAL实验'], '规模或WAL配置不符')
        require(sum(v['次数'] for v in row['调用延迟'].values()) == 1200000, '业务计数不完整')
    for key in ('软件清单SHA256', '请求轨迹SHA256', '最新值清单SHA256'):
        require(len({a[key] for a in arms.values()}) == 1, '两组身份不同：' + key)
    require(arms['greedy']['实际改选次数'] == 0, 'Greedy不应实际改选')
    keys = ('ZC重置', 'RR重置', '总迁移MiB', 'ZC迁移MiB', 'RR迁移MiB',
            '应急单区次数', '空间紧急次数', '实际改选次数')
    delta = {k: arms['retention'][k] - arms['greedy'][k] for k in keys}
    latency = {op: {k: arms['retention']['调用延迟'][op][k] - arms['greedy']['调用延迟'][op][k]
                   for k in ('平均微秒', 'p95微秒', 'p99微秒')} for op in ('READ', 'UPDATE')}
    business = {mode: dict(row['分阶段维护']['业务']) for mode, row in arms.items()}
    for row in business.values():
        row['总迁移MiB'] = row['ZC迁移MiB'] + row['RR迁移MiB']
    # 旧摘要可能没有吞吐字段；只能明确标为缺失，不能用延迟或计划时长补造。
    rates={mode:row.get('吞吐量') for mode,row in arms.items()}
    comparable=all(rates.values())
    if comparable:
        require(len({(r['配置限速ops每秒'],r['限速计划']) for r in rates.values()})==1,'吞吐限速配置不同')
    delta_rate=(rates['retention']['平均ops每秒']-rates['greedy']['平均ops每秒']) if comparable else None
    return dict(配对核验通过=True, 模式顺序=list(MODES),
        各组摘要={mode: {k: row[k] for k in (*keys, '调用延迟', '分阶段维护')} for mode, row in arms.items()},
        相对Greedy差值=delta, 业务阶段=business,
        业务阶段相对Greedy={k: business['retention'][k] - business['greedy'][k] for k in business['greedy']},
        调用延迟相对Greedy微秒=latency,
        吞吐量= rates,吞吐量相对Greedyops每秒=delta_rate,
        发生实际模型改选=arms['retention']['实际改选次数'] > 0,
        结论边界='单次配对不证明稳态或统计显著性；零改选时不得将性能差异归因于模型。优先看业务总迁移、RR、ZC和延迟，不只看RR下降。',
        额外边界='相同请求不保证压实调度和物理布局相同；无WAL；Update包含Get再Put；复制量为逻辑字节而非NAND写入。')


def compare(root):
    root = Path(root)
    require(len({sha(root / mode / '20261017/workload.properties') for mode in MODES}) == 1,
            '两组负载配置不同')
    arms = {mode: json.loads((root / mode / '独立核验.json').read_text()) for mode in MODES}
    for mode,row in arms.items():
        raw=root/mode/'20261017'
        properties=dict(s.split('=',1) for s in (raw/'workload.properties').read_text().splitlines()
            if '=' in s and not s.startswith('#'))
        # 只在内存补齐旧摘要，保留原始归档和既有核验文件。
        require(all((raw/name).read_text().strip()=='0' for name in ('business.exit','verify.exit','runner.exit')),
            '未成功的运行不能报告正式吞吐量')
        row['吞吐量']=throughput((raw/'ycsb.log').read_text(),row['计划业务操作数'],properties)
    return compare_results(arms)
