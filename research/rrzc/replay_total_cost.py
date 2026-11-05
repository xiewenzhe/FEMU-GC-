"""用真实归档评分逐轮重放C++总复制实验核；不把改选推测写成维护收益。"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from analyze_joint_opportunity import audit, compare, model_decisions, require

ROOT=Path(__file__).resolve().parent
CASES=('opportunity','retire','stopped','low-yield')


def replay(binary,rounds):
    results=[]
    for item in rounds:
        rows=item['成本'];decision=item['决策']
        inputs=[(int(r['Zone']),int(r['有效']),int(r['有效'])+int(r['回收']),
                 int(r['RR抵扣']),int(r['回收']),int(r['净复制']),int(r['安全'])) for r in rows]
        raw=str(len(inputs))+'\n'+''.join(' '.join(map(str,r))+'\n' for r in inputs)
        lines=subprocess.check_output([str(binary),'replay'],input=raw,text=True).splitlines()
        good,pool,count=map(int,lines[0].split())
        actual=[tuple(map(int,r.split())) for r in lines[1:]]
        safe=[r for r in inputs if r[6]]
        expected_pool=sum(r[3] for r in safe)
        ordered=sorted(safe,key=lambda r:(Fraction(r[1]+expected_pool-r[3],r[4]),r[1],r[0]))
        expected=[(r[0],expected_pool-r[3],r[1]+expected_pool-r[3]) for r in ordered]
        require(good==1 and pool==expected_pool and count==len(safe) and actual==expected,
                'C++重放与独立精确分数计算不一致')
        zone=actual[0][0] if actual else 0
        results.append(dict(轮次=int(decision['轮次']),原实际Zone=int(decision['实际Zone']),
            实验核建议Zone=zone,建议是否变化=zone!=int(decision['实际Zone']),
            安全池预计RR字节=pool,排序=[dict(Zone=z,未选项预计RR字节=deferred,总预计复制字节=total)
                for z,deferred,total in actual]))
    return results


def run(root,scenarios,logs=()):
    source=ROOT/'tests/test_total_cost_selector.cc';header=ROOT/'model/rrzc_total_cost_selector.h'
    report=dict(口径='原策略真实轨迹上的静态选择重放，不是新策略FEMU运行或反事实收益',
        修改='仅比较评分目标，不修改现有RR/留存预测，不接线当前ZenFS',
        身份={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (source,header,ROOT/'model/rrzc_direct_selector.h')},场景={},其他日志=[])
    with tempfile.TemporaryDirectory(prefix='rrzc-total-replay-') as tmp:
        binary=Path(tmp)/'model'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(source),'-o',str(binary)],check=True,capture_output=True)
        report['模型编译及合成边界测试']=subprocess.check_output([str(binary)],text=True).strip()
        for scenario in scenarios:
            arms=[audit(root/f'{scenario}-{mode}'/scenario,mode,scenario) for mode in ('greedy','direct')]
            actual=compare(*arms);outcomes=replay(binary,arms[1]['实际模型成本及决策'])
            report['场景'][scenario]=dict(原策略配对验收通过=actual['配对验收通过'],重放=outcomes,
                改选轮次数=sum(r['建议是否变化'] for r in outcomes),
                原策略真实复制字节={mode:actual[mode]['总复制字节'] for mode in ('Greedy','直接模型')},
                实验核真实RR次数=None,实验核真实复制字节=None,
                边界='重放未执行迁移；即使建议未变，也不把原运行指标冒充新机制测量')
        for log in logs:
            lines=log.read_text().splitlines();rounds=model_decisions(lines)
            require(rounds,'其他日志没有可重放的真实直接模型成本记录')
            result=replay(binary,rounds)
            report['其他日志'].append(dict(路径=str(log),SHA256=hashlib.sha256(log.read_bytes()).hexdigest(),
                轮次数=len(result),改选轮次数=sum(r['建议是否变化'] for r in result),重放=result,
                边界='未重新审计整轮YCSB，仅重放已记录安全评分，不证明完整新策略收益'))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--scenarios',choices=CASES,nargs='+',required=True)
    parser.add_argument('--log',action='append',type=Path,default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    require(not args.output.exists(),'输出已存在，拒绝覆盖')
    result=run(args.root,args.scenarios,args.log)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(场景={k:dict(改选轮次数=v['改选轮次数'],原策略真实复制字节=v['原策略真实复制字节'])
        for k,v in result['场景'].items()},其他日志=[{k:v for k,v in r.items() if k not in ('重放','边界')}
        for r in result['其他日志']],结果=str(args.output)),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
