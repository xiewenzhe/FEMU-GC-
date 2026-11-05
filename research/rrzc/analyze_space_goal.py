"""独立核对真实空间收尾成本，不把共同下限冒称完全相同终态。"""
import json
from pathlib import Path
from analyze_reproducibility import accounting
from analyze_joint_value import parse

GOAL=1<<30
COUNTERS=('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取',
          '阈值触发事件','被阻止读取')


def load(path):return json.loads(path.read_text())


def verdict(a,b):
    assert min(a['终点空闲字节'],b['终点空闲字节'])>=GOAL
    assert a['终点有效字节']==b['终点有效字节']
    if b['业务加收尾迁移字节']<a['业务加收尾迁移字节'] and b['终点空闲字节']>=a['终点空闲字节']:
        return '本次受控路径少复制且结束可用空间不低；单轮不代表稳定性能收益'
    return '未证明少复制且空间不低的净收益；须同时报告复制差及超额空间差'


def arm(root):
    root=Path(root)
    rows=[json.loads(s) for s in (root/'goal.log').read_text().splitlines() if s.startswith('{')]
    assert rows and rows[0]['序号']==0 and rows[-1]['达标']
    assert [r['序号'] for r in rows]==list(range(len(rows))) and len(rows)<=33
    assert all(r['目标字节']==GOAL and r['有效字节']==rows[0]['有效字节'] for r in rows)
    assert all(not r['达标'] for r in rows[:-1]),'达标后仍在继续维护'
    log=(root/'goal.zenfs.log').read_text()
    decisions=[s for s in log.splitlines() if '共同观测实际决策：' in s]
    assert all('执行Greedy=1，' in s for s in decisions)
    assert '受控GC批次检查：启用=1，原启动水平=20%' in log
    assert '主机端 RR=1' in log and '严格RR主机配置：启用=1' in log
    ledger=accounting(root,'goal.zenfs.log');business=accounting(root)
    assert ledger['复制日志与维护汇总相同'] and ledger['映射提交失败数']==0
    assert ledger['ZC累计账目闭合'] and ledger['RR累计账目闭合']
    chains=parse(log.splitlines())
    assert len(chains['复制'])==ledger['成功复制事件']
    space=load(root/'space.goal.json');before=load(root/'space.business.json')
    assert space['空闲字节']>=GOAL and space['有效字节']==before['有效字节']
    assert rows[-1]['空闲字节']==space['空闲字节'],'关机前后空间口径不一致'
    old=load(root/'strict.business.json');new=load(root/'strict.goal.json')
    assert new['启用']==1 and new['历史最高RC']<=1024
    delta={k:new[k]-old[k] for k in COUNTERS};assert min(delta.values())>=0
    return dict(业务=business,空间收尾=ledger,收尾设备增量=delta,
        收尾检查数=len(rows)-1,收尾采样=rows,
        业务结束空闲字节=before['空闲字节'],终点空闲字节=space['空闲字节'],
        超额空间字节=space['空闲字节']-GOAL,终点有效字节=space['有效字节'],
        业务加收尾迁移字节=business['总迁移字节']+ledger['总迁移字节'],
        业务加收尾ZC次数=business['ZC次数']+ledger['ZC次数'],
        业务加收尾RR次数=business['RR次数']+ledger['RR次数'],
        业务加收尾成功Reset=business['全部成功Reset次数']+ledger['全部成功Reset次数'],
        收尾复制路径=[{k:c[k] for k in ('维护','文件','源Zone','目标Zone','源片段','目标片段','字节')}
            for c in chains['复制']])


def repeat_guard(a,b):
    first,second=arm(a),arm(b)
    keys=('空间收尾','收尾设备增量','收尾检查数','业务结束空闲字节','终点空闲字节',
          '终点有效字节','收尾复制路径')
    equal={k:first[k]==second[k] for k in keys}
    equal['设备Zone终态']=load(Path(a)/'zones.goal.json')==load(Path(b)/'zones.goal.json')
    equal['ZenFS空间终态']=load(Path(a)/'space.goal.json')==load(Path(b)/'space.goal.json')
    return dict(通过=all(equal.values()),核对=equal,
        边界='同配置Greedy业务后空间收尾重复；不代表完整NAND快照或正常并发性能')


def analyze(output):
    output=Path(output);state=load(output/'状态.json');archive=load(output/'阶段归档.json')
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
    source=output/state['标签'];data={tag:arm(source/tag) for tag in ('g1','g2')}
    a,b=data['g1'],data['g2']
    result=dict(目标字节=GOAL,收尾执行='两组共同Greedy；保留GC20%及严格RR1024',各组=data,
        模型减Greedy={k:b[k]-a[k] for k in ('业务加收尾迁移字节','业务加收尾ZC次数',
            '业务加收尾RR次数','业务加收尾成功Reset','终点空闲字节')},
        结论=verdict(a,b),
        边界='固定业务请求后的实际空间债务测量；收尾无前台读写、重新挂载且检查时机受控。'
            '终点仅共同下限，必须报告超额空间；不是原正常并发性能或完全相同资源状态。'
            '设备增量包含业务后只读空间探针、收尾挂载和维护；不将Block擦除推导值冒称实测。')
    (output/'共同空间目标核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 共同最低空间目标对照结果','',result['结论'],'',
        '| 指标 | Greedy | 既有V1模型 |','|---|---:|---:|']
    for title,key,scale in [('业务有效迁移MiB','业务',1),('收尾有效迁移MiB','空间收尾',1)]:
        lines.append(f"| {title} | {a[key]['总迁移字节']/2**20:.3f} | {b[key]['总迁移字节']/2**20:.3f} |")
    for title,key,scale in [('业务加收尾迁移MiB','业务加收尾迁移字节',2**20),
        ('业务加收尾ZC次数','业务加收尾ZC次数',1),('业务加收尾RR次数','业务加收尾RR次数',1),
        ('业务加收尾成功Zone Reset','业务加收尾成功Reset',1),
        ('结束可用空间MiB','终点空闲字节',2**20),('超过1GiB目标MiB','超额空间字节',2**20)]:
        lines.append(f'| {title} | {a[key]/scale:.3f} | {b[key]/scale:.3f} |')
    lines+=['',result['边界'],'',
        '业务期API计时仍见受控配对分析；收尾无前台请求，不能将其虚构为一组业务IO延迟。',
        'Zone Reset包含自然回收及挂载路径，不等于ZC+RR，也不是直接测得的Block擦除次数。',
        '逐轮收尾空间、分类设备读取、复制片段路径和差值见共同空间目标核验.json。',
        '本结果是一对受控路径，不代表正常并发或统计稳定收益。']
    (output/'共同空间目标分析.md').write_text('\n'.join(lines)+'\n')
    return result
