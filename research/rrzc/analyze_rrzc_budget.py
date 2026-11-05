"""只读复核额外搬运预算阻塞：当前V2与取消固定上限的离线诊断，禁止冒充闭环收益。"""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from analyze_net_cost_replay import prepare,rr_barriers
from analyze_v1_shadow_stream import fields,require,eta
from analyze_v1_growth_stream import qualifies,same_generation_outcome
from analyze_short_shadow_stream import short_eta
from analyze_ycsb_retention import audit,sha
from guarded_rounds import reserve_check
from replay_tail_guard import guard

MIB=1<<20
SECOND=10**9


def compare_cost(valid,greedy_valid,capacity,credit):
    require(0<greedy_valid<=valid<capacity and 0<=credit<=valid,'净成本输入不合法')
    reclaimed=capacity-valid;greedy_reclaimed=capacity-greedy_valid
    # 要求抵扣后单位回收成本严格低于Greedy；不是只要求抵扣大于额外字节。
    difference=valid*greedy_reclaimed-greedy_valid*reclaimed
    needed=difference//greedy_reclaimed+1
    return dict(额外迁移字节=valid-greedy_valid,少回收字节=greedy_reclaimed-reclaimed,
        候选可回收字节=reclaimed,Greedy可回收字节=greedy_reclaimed,
        严格胜过Greedy所需最小抵扣字节=needed,模型折扣抵扣字节=credit,
        单位回收净成本更低=(valid-credit)*greedy_reclaimed<greedy_valid*reclaimed,
        候选净复制每回收字节=(valid-credit)/reclaimed,
        Greedy复制每回收字节=greedy_valid/greedy_reclaimed)


def check_known_cost(row,forecast,cost):
    c,b,g=(row[k] for k in ('空间','同块','增长'))
    if not cost['可知']:return
    t=max(short_eta(b,g),eta(b));v=int(c['有效字节']);horizon=10*SECOND
    loss=max((int(forecast[k])*t+int(forecast[interval])-1)//int(forecast[interval])
        for k,interval in (('十秒减少字节','十秒实际间隔纳秒'),('三十秒减少字节','三十秒实际间隔纳秒')))
    retained=v-min(v,loss);credit=retained*(horizon-t)//horizon
    require((cost['ETA纳秒'],cost['预计保留字节'],cost['折扣抵扣字节'],cost['净复制字节'],cost['回收字节'])==
        (t,retained,credit,v-credit,int(c['容量字节'])-v),'独立整数公式与C++成本不符')


def run(root,binary):
    root=Path(root);log=root/'20261017/zenfs.run.log'
    evaluation=audit(root/'20261017','greedy',nowal=True,expected_ops=1200000,
        single_recovery=True,space_emergency=True,independent_ratio='50/50',update_range=(4096,258048))
    require(evaluation['验收通过'],'真实归档未通过基础核验')
    raw=log.read_text().splitlines()
    diagnosis,groups,old,original,payload,_,_=prepare(raw)
    def replay(*args):
        p=subprocess.run([str(binary),*args],input=payload,text=True,capture_output=True,check=True,timeout=30)
        rows=[json.loads(s) for s in p.stdout.splitlines()]
        require([str(r['轮次']) for r in rows]==list(groups),'C++回放轮次不完整或顺序不同')
        return {str(r['轮次']):r for r in rows}
    default=replay();diagnostic=replay('--diagnose-without-extra-cap')
    forecasts={};pools={};parts=defaultdict(list);files=defaultdict(list);retired=defaultdict(list)
    for number,line in enumerate(raw,1):
        for marker,target in (('留存收益候选：',forecasts),('独立约束预留：',pools)):
            if marker in line:
                row=fields(line,marker);key=(row['轮次'],row['Zone']) if marker=='留存收益候选：' else row['轮次']
                require(key not in target,'候选或预留记录重复');target[key]=row
        if '独立约束片段：' in line:
            row=fields(line,'独立约束片段：');parts[row['轮次'],row['Zone']].append(row)
        if 'V1影子文件：' in line:
            row=fields(line,'V1影子文件：');files[row['轮次'],row['Zone']].append(row)
        if 'V1后续文件退役：' in line:
            row=fields(line,'V1后续文件退役：');retired[row['文件编号']].append(number)
    proposals=[];rounds=[]
    for rid,rows in groups.items():
        g=next(row for row in rows if row['空间']['Zone']==row['GreedyZone'])
        gv=int(g['空间']['有效字节']);greedy_zone=int(g['空间']['Zone'])
        require(default[rid]['GreedyZone']==diagnostic[rid]['GreedyZone']==greedy_zone,'Greedy输入改变')
        costs={str(c['Zone']):c for c in diagnostic[rid]['成本']}
        rounds.append(dict(轮次=int(rid),当前V2=default[rid],取消固定预算诊断=diagnostic[rid]))
        for row in rows:
            c,b,s=(row[k] for k in ('空间','同块','短期'));zone=c['Zone']
            if not (zone!=str(greedy_zone) and qualifies(c) and s['短期预测可知']=='1'
                    and int(s['短期达阈值纳秒'])<=10*SECOND):continue
            v=int(c['有效字节']);f=forecasts[rid,zone];cost=costs.get(zone)
            if cost is not None:check_known_cost(row,f,cost)
            def candidate(z):
                chosen=next(r for r in rows if r['空间']['Zone']==z)
                pp=parts[rid,z]
                return dict(有效字节=chosen['空间']['有效字节'],
                    片段=[dict(次序=p['次序'],逻辑字节=p['逻辑字节']) for p in pp])
            alignments={int(p['对齐字节']) for z in (str(greedy_zone),zone) for p in parts[rid,z]}
            require(len(alignments)==1,'对齐输入未知或不同')
            pool=pools[rid];stable=reserve_check(pool)
            tail=guard(candidate(str(greedy_zone)),candidate(zone),pool,next(iter(alignments)))
            tail.pop('原RR紧迫度及额外有效预算不变',None)
            tail['仅检查尾部_不判断额外预算']=True
            tail['候选总上界装入后剩余字节']=int(pool['剩余字节'])-int(s['总上界字节'])
            chosen=diagnostic[rid]['会改选'] and diagnostic[rid]['建议Zone']==int(zone)
            optimistic_eta=max(int(s['短期达阈值纳秒']),eta(b))
            optimistic_credit=v*(10*SECOND-optimistic_eta)//(10*SECOND)
            proposal=dict(轮次=int(rid),原始决策行号=original[rid],Zone=int(zone),
                设备会话=b['设备会话'],Reset代际=int(b['设备代际']),块编号=int(b['块编号']),
                当前RC=int(b['当前RC']),已达到阈值=int(b['当前RC'])>=4096,
                GreedyZone=greedy_zone,Greedy有效MiB=gv/MIB,候选有效MiB=v/MIB,
                额外迁移MiB=(v-gv)/MIB,当前固定预算MiB=16,
                短期ETA秒=int(s['短期达阈值纳秒'])/SECOND,窗口ETA秒=eta(b)/SECOND,
                当前留存输入=f,当前V2净成本=cost,
                假定全部有效数据保留时的抵扣上界字节=optimistic_credit,
                最乐观上界是否可胜Greedy=compare_cost(v,gv,int(c['容量字节']),optimistic_credit)['单位回收净成本更低'],
                收益与空间比较=compare_cost(v,gv,int(c['容量字节']),cost['折扣抵扣字节']) if cost and cost['可知'] else None,
                取消固定预算后的V2会选择此候选=bool(chosen),
                决策时尾部评估=tail,预留双点稳定=stable,
                源复核='原路径未产生建议，因此未执行反事实源复核；尾部可容纳不等于实时源复核已通过',
                可进入实时复核的离线建议=bool(chosen and stable and tail['保留原建议']))
            # 所有成本及建议确定后才关联旧轨迹后续事件；不得将未来RR或文件失效输入模型。
            event=same_generation_outcome(raw,original[rid],int(zone))
            file_rows=files[rid,zone]
            require(sum(int(r['区内有效字节']) for r in file_rows)==v,'文件覆盖不完整')
            end=event.get('行号',len(raw)+1)
            deaths=[dict(文件编号=r['文件编号'],决策时区内有效字节=int(r['区内有效字节']),
                首事件前退役行号=[n for n in retired[r['文件编号']] if original[rid]<n<end]) for r in file_rows]
            if '行号' in event:
                fmt='%Y/%m/%d-%H:%M:%S.%f'
                event['距决策秒']=(datetime.strptime(raw[end-1][:26],fmt)-datetime.strptime(raw[original[rid]-1][:26],fmt)).total_seconds()
            proposal['事后标签_不参与准入']=dict(同代际首事件=event,当时文件=deaths,
                边界='反事实改选会改变迁移目标、布局与后续读数；观察到RR不证明能减少相同数量的RR或总搬运')
            proposals.append(proposal)
    sources=[Path(__file__),Path(__file__).parent/'tests/net_cost_replay.cc',
        Path(__file__).parent/'model/rrzc_net_cost_selector.h',binary]
    return dict(真实归档验收通过=True,原始日志=str(log),原始日志SHA256=sha(log),
        回放输入SHA256=hashlib.sha256(payload.encode()).hexdigest(),
        源码与二进制SHA256={str(p):sha(p) for p in sources},
        实际运行='Greedy；本次只是离线分析，没有部署、改参数或启动设备实验',
        诊断预算解释='取消16MiB额外门槛，以256-192=64MiB覆盖原合格候选；仅用于离线隔离阻塞条件，不是新的在线阈值或调参搜索',
        正常轮数=len(groups),默认V2建议次数=sum(r['会改选'] for r in default.values()),
        取消固定预算后V2建议次数=sum(r['会改选'] for r in diagnostic.values()),
        有尾部与预留证据的待复核次数=sum(r['可进入实时复核的离线建议'] for r in proposals),
        其中已达到阈值次数=sum(r['可进入实时复核的离线建议'] and r['已达到阈值'] for r in proposals),
        原实际拒绝原因=dict(Counter(d['原因'] for d in evaluation['决策'])),
        RR机会障碍=rr_barriers(diagnosis['后续RR源观测']),
        机会逐项=proposals,全部轮次回放=rounds,
        结论边界='预测抵扣和单位回收净成本是未经闭环验证的模型假设，不是实际节省；不累加三次反事实收益，不假设被推迟Greedy无需后续维护。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--binary',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.root.resolve(),args.binary.resolve()),ensure_ascii=False,indent=2))
