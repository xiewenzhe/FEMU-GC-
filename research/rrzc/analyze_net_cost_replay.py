"""配对候选漏斗与V2真实输入回放；不将回放建议当作设备收益。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
from analyze_v1_shadow_stream import fields, require
from analyze_ycsb_candidate_overlap import analyze
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay


def rr_barriers(events):
    counts=Counter()
    evidence=[]
    for row in events:
        prior=row['此前满区观测']
        if row['首次正常选区决策之前']:
            reason='首次正常ZC之前，不能靠当时尚未执行的选区处理'
        elif prior is None:
            reason='没有可关联的同代际满区观测，不能判断'
        elif not prior['回收预算资格']:
            reason='最近同代际GC观测不满足原回收预算'
        else:
            reason='最近同代际预算合格，仍需逐项核对其他资格和时序'
        counts[reason]+=1
        evidence.append(dict(RR序号=row['序号'],Zone=row['Zone'],原因=reason,
            RR有效MiB=row['有效MiB'],最近GC有效MiB=prior['有效MiB'] if prior else None,
            最近GC原比例资格=prior['原比例门槛资格'] if prior else None))
    return dict(分类=dict(counts),证据=evidence,
        边界='只关联最近一次可信同代际GC观测，不证明该Zone在全部历史中从未合格；不因观察到未来RR就纳入当时决策。')


def prepare(raw):
    # 公共恢复有独立资格，不能混作正常模型候选；旧分析器先验证它再分离。
    lines,space=space_replay(raw)
    lines,single=single_replay(lines)
    diagnosis=analyze(lines)
    forecasts={};old={};original={}
    for number,line in enumerate(raw,1):
        if '留存收益候选：' in line:
            row=fields(line,'留存收益候选：');key=(row['轮次'],row['Zone'])
            require(key not in forecasts,'留存记录重复');forecasts[key]=row
        if '留存收益建议：' in line:
            row=fields(line,'留存收益建议：')
            require(row['轮次'] not in old,'留存建议重复');old[row['轮次']]=row
        if '独立约束决策：' in line:
            row=fields(line,'独立约束决策：');original[row['轮次']]=number
    groups=defaultdict(list)
    for row in diagnosis['候选明细']:
        row['原始日志决策行号']=original[row['轮次']]
        groups[row['轮次']].append(row)
    records=[]
    for rid,rows in groups.items():
        require(rid in old,'缺少旧留存建议')
        records.append(f'{rid} {len(rows)}')
        for row in rows:
            c,b,g=(row[key] for key in ('空间','同块','增长'))
            require(c['读数阈值']=='4096','回放仅登记当前4096阈值')
            f=forecasts[rid,c['Zone']]
            require(f['有效字节']==c['有效字节'],'有效量输入不一致')
            values=[c['Zone'],c['有效字节'],c['容量字节'],c['覆盖资格'],c['空目标资格'],
                b['统计准入'],b['当前RC'],b['十秒真实增量'],b['三十秒真实增量'],b['窗口标志'],
                g['增量可知'],g['真实增量'],g['间隔纳秒'],g['短期达阈值纳秒'],
                f['可知'],f['十秒减少字节'],f['三十秒减少字节'],f['十秒实际间隔纳秒'],
                f['三十秒实际间隔纳秒'],f['预计保留字节']]
            require(all(str(int(v))==v and 0<=int(v)<2**64 for v in values),'非规范非负整数输入')
            records.append(' '.join(values))
    return diagnosis,groups,old,original,'\n'.join(records)+'\n',single,space


def audit_arm(root,binary):
    log=root/'20261017/zenfs.run.log'
    raw=log.read_bytes()
    diagnosis,groups,old,original,inputs,single,space=prepare(raw.decode().splitlines())
    process=subprocess.run([str(binary)],input=inputs,text=True,capture_output=True,check=True,timeout=30)
    results=[json.loads(s) for s in process.stdout.splitlines()]
    require(len(results)==len(groups),'C++回放轮数不完整')
    require([str(r['轮次']) for r in results]==list(groups),'回放轮次顺序不同')
    changed=[]
    for row in results:
        rid=str(row['轮次']);prior=old[rid]
        require(row['GreedyZone']==int(groups[rid][0]['GreedyZone']),'C++原Greedy重算不符')
        row['原始日志决策行号']=original[rid]
        row['旧留存建议Zone']=int(prior['新建议Zone'])
        row['旧留存会改选']=int(prior['会改选'])
        if (row['建议Zone'],row['会改选'])!=(row['旧留存建议Zone'],row['旧留存会改选']):
            changed.append(row['轮次'])
    core={k:diagnosis[k] for k in ('GC检查次数','GC进入选区次数','正常决策轮数','RR次数',
        '首次正常选区之前RR次数','候选筛选','决策原因','RR覆盖保护延后次数')}
    return dict(原始日志=str(log.resolve()),原始日志SHA256=hashlib.sha256(raw).hexdigest(),
        正常候选诊断=core,RR机会障碍=rr_barriers(diagnosis['后续RR源观测']),
        公共单区恢复次数=sum(r['存在']=='1' for r in single),空间紧急次数=len(space),
        V2回放轮数=len(results),V2回放建议改选数=sum(r['会改选'] for r in results),
        与旧留存建议不同轮次=changed,回放决策=results,
        回放输入SHA256=hashlib.sha256(inputs.encode()).hexdigest(),
        回放边界='真实旧轨迹上逐轮局部建议，不是V2闭环执行。保留原资格与十秒准入，未计入后续源及尾部复核；不预测RR节省次数或迁移总量。',
        预测边界='复用旧日志已核验的连续历史标志，不新增未来信息；线性退役与时间折扣仍是假设，不是经过校准的概率。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pair',type=Path)
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    require(not args.output.exists(),'输出已存在，拒绝覆盖')
    require(json.loads((args.pair/'两组对照.json').read_text())['配对核验通过'],'原配对未通过')
    result={mode:audit_arm(args.pair/mode,args.binary.resolve()) for mode in ('greedy','retention')}
    sources=[Path(__file__),Path(__file__).parent/'model/rrzc_net_cost_selector.h',
             Path(__file__).parent/'tests/net_cost_replay.cc',args.binary]
    result['本轮源与二进制SHA256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    for mode in ('greedy','retention'):
        row=result[mode]
        print(mode,json.dumps({k:row[k] for k in ('正常候选诊断','RR机会障碍',
            'V2回放轮数','V2回放建议改选数','与旧留存建议不同轮次')},ensure_ascii=False))


if __name__=='__main__':main()
