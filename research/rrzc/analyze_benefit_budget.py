"""回放已实现收益预算，核验建议与执行预算的整数界限；不模拟反事实维护收益。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from analyze_rrzc_budget import run as review
from analyze_net_cost_replay import prepare
from analyze_v1_growth_stream import qualifies
from analyze_v1_shadow_stream import require
from analyze_ycsb_retention import sha


def analyze(root,binary,legacy_binary=None):
    base=review(root,binary)
    raw=(root/'20261017/zenfs.run.log').read_text().splitlines()
    _,groups,_,_,payload,_,_=prepare(raw)
    run=subprocess.run([str(binary),'--benefit-budget'],input=payload,text=True,
        capture_output=True,check=True,timeout=30)
    rows=[json.loads(s) for s in run.stdout.splitlines()]
    require([str(r['轮次']) for r in rows]==list(groups),'收益预算回放轮次不完整')
    checked=[]
    for row,previous in zip(rows,base['全部轮次回放']):
        original=previous['取消固定预算诊断']
        require(all(row[k]==original[k] for k in ('轮次','GreedyZone','建议Zone','会改选','额外字节','预测纳秒','成本')),
            '新实现与既有净成本诊断不一致')
        if row['会改选']:
            candidates=groups[str(row['轮次'])]
            chosen=next(r['空间'] for r in candidates if int(r['空间']['Zone'])==row['建议Zone'])
            greedy=next(r['空间'] for r in candidates if int(r['空间']['Zone'])==row['GreedyZone'])
            require(qualifies(chosen),'收益预算越过原资格')
            cost=next(c for c in row['成本'] if c['Zone']==row['建议Zone'])
            cap,gv=int(greedy['容量字节']),int(greedy['有效字节'])
            upper=max(int(r['空间']['有效字节'])-gv for r in candidates if qualifies(r['空间']))
            limit=(cost['折扣抵扣字节']*(cap-gv)-1)//cap
            require(row['执行预算字节']==min(limit,upper) and row['额外字节']<=row['执行预算字节'],
                '收益预算整数界限或传递不正确')
            checked.append(row['轮次'])
    unchanged=None
    if legacy_binary is not None:
        old=subprocess.run([str(legacy_binary)],input=payload,text=True,capture_output=True,check=True,timeout=30)
        new=subprocess.run([str(binary)],input=payload,text=True,capture_output=True,check=True,timeout=30)
        require(old.stdout==new.stdout,'默认入口与扩展前二进制输出不一致')
        unchanged=dict(原二进制=str(legacy_binary),SHA256=sha(legacy_binary),默认输出逐字节相同=True)
    return dict(真实归档核验及诊断=base,
        收益预算实现回放=dict(完整轮数=len(rows),建议改选次数=sum(r['会改选'] for r in rows),
            建议轮次=checked,执行预算独立复核通过=True,逐轮结果=rows),
        旧默认入口兼容=unchanged,
        实现源码SHA256={str(p):sha(p) for p in (Path(__file__),Path(__file__).parent/'model/rrzc_benefit_budget.h',
            Path(__file__).parent/'zenfs-benefit-budget.patch',binary)},
        输入SHA256=hashlib.sha256(payload.encode()).hexdigest(),
        边界='已编译的独立C++模型与真实旧输入回放，不是新策略设备实验；补丁尚未应用或完整编译客机ZenFS，未验证实时源复核通过率与闭环RR/迁移/吞吐收益。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--legacy-binary',type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.root.resolve(),args.binary.resolve(),
        args.legacy_binary.resolve() if args.legacy_binary else None),ensure_ascii=False,indent=2))
