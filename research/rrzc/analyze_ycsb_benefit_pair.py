"""核验同库同负载的Greedy/收益预算YCSB对照，接受零改选和负结果。"""
from collections import Counter
import json
from pathlib import Path
from analyze_v1_shadow_stream import fields,require
from analyze_ycsb_retention import audit,sha
from benefit_log import BenefitLog

MODES=('greedy','benefit')


def audit_run(root,mode):
    require(mode in MODES,'不是本轮策略')
    result=audit(root,mode,nowal=True,expected_ops=1200000,single_recovery=True,
        space_emergency=True,independent_ratio='50/50',update_range=(4096,258048),
        benefit_budget=mode=='benefit')
    raw=(root/'zenfs.run.log').read_text()
    if mode=='greedy':require('收益预算配置：' not in raw,'Greedy组意外启用收益预算')
    suggestions=[fields(s,'收益预算建议：') for s in raw.splitlines() if '收益预算建议：' in s]
    decisions=result['决策'];switched=[d for d in decisions if d['独立核验改选']]
    result['收益预算诊断']=dict(建议改选次数=sum(r['会改选']=='1' for r in suggestions),
        建议原因计数=dict(Counter(r['原因'] for r in suggestions)),
        实际改选额外有效字节=sum(int(d['实际额外有效字节']) for d in switched),
        建议后被执行护栏拒绝次数=sum(d['原会改选']=='1' and d['实际改选']=='0' for d in decisions),
        执行护栏原因计数=dict(Counter(d['原因'] for d in decisions)),
        边界='额外有效字节为相对当轮Greedy的选择差额，不是全程迁移差额；预测抵扣不是已节省的RR。')
    return result


def compare_results(arms):
    require(set(arms)==set(MODES),'配对组缺失或混入其他策略')
    for mode,r in arms.items():
        require(r['验收通过'] and r['模式']==mode and r['计划业务操作数']==1200000 and
            r['无WAL实验'],'策略、规模或正确性不符')
        require(set(r['调用延迟'])=={'READ','UPDATE'} and
            sum(v['次数'] for v in r['调用延迟'].values())==1200000,'业务调用计数不完整')
        require(r['吞吐量'] and r['吞吐量']['平均ops每秒']>0,'缺实际Run吞吐量')
    for key in ('软件清单SHA256','请求轨迹SHA256','最新值清单SHA256'):
        require(len({r[key] for r in arms.values()})==1,'配对身份不同：'+key)
    require(arms['greedy']['实际改选次数']==0,'Greedy不应改选')
    require(len({(r['吞吐量']['配置限速ops每秒'],r['吞吐量']['限速计划']) for r in arms.values()})==1,
        '两组吞吐限速配置不符')
    keys=('ZC重置','RR重置','总迁移MiB','ZC迁移MiB','RR迁移MiB','应急单区次数','空间紧急次数','实际改选次数')
    business={m:dict(r['分阶段维护']['业务']) for m,r in arms.items()}
    for r in business.values():r['总迁移MiB']=r['ZC迁移MiB']+r['RR迁移MiB']
    return dict(配对核验通过=True,模式顺序=list(MODES),
        各组摘要={m:{k:r[k] for k in (*keys,'调用延迟','吞吐量','分阶段维护','收益预算诊断')} for m,r in arms.items()},
        相对Greedy差值={k:arms['benefit'][k]-arms['greedy'][k] for k in keys},
        业务阶段=business,业务阶段相对Greedy={k:business['benefit'][k]-business['greedy'][k] for k in business['greedy']},
        调用延迟相对Greedy微秒={op:{k:arms['benefit']['调用延迟'][op][k]-arms['greedy']['调用延迟'][op][k]
            for k in ('平均微秒','p95微秒','p99微秒')} for op in ('READ','UPDATE')},
        吞吐量相对Greedyops每秒=arms['benefit']['吞吐量']['平均ops每秒']-arms['greedy']['吞吐量']['平均ops每秒'],
        发生实际模型改选=arms['benefit']['实际改选次数']>0,
        结论边界='单次配对，不证明稳态、显著性或普适性；零改选不能将性能波动归因于模型。优先看业务总迁移、RR/ZC和延迟，不能只看RR下降。',
        口径='无WAL，Update包含Get再Put；迁移为逻辑复制字节；Run吞吐包含请求生成、审计、限速与Cleanup，限速1000ops/s，不是最大设备IOPS。')


def compare(root):
    root=Path(root)
    require(len({sha(root/m/'20261017/workload.properties') for m in MODES})==1,'两组负载不同')
    return compare_results({m:json.loads((root/m/'独立核验.json').read_text()) for m in MODES})
