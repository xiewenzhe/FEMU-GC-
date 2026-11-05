"""期望RR模型真实配对验收：逐条独立评分、同库同初态、载荷和维护账目核对。"""
import hashlib
import json
from pathlib import Path

from analyze_joint_opportunity import audit,compare,require,fields
from analyze_window_credit import audit_scores
from analyze_expected_rr_probe import read_trace,clock_offset


def analyze_arm(root,mode,scenario):
    require(mode in ('greedy','expected'),'未知期望RR实验策略')
    root=Path(root);actual=audit(root,'direct',scenario)
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines();enabled=int(mode=='expected')
    scores=audit_scores(lines,'window' if enabled else 'greedy',persistent=bool(enabled),expected_rr=bool(enabled))
    require((root/'策略.txt').read_text().splitlines()==[
        '策略='+mode,'Greedy开关='+str(1-enabled),'窗口收益开关='+str(enabled),
        '稳定收益开关=0','RR公平队列=1','持续读开关='+str(enabled),'期望RR开关='+str(enabled),
        '只读轨迹开关=1'],'策略登记与实际执行不符')
    require('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in raw and
        raw.count('RR公平队列服务：')==raw.count('RR 开始：'),'共享RR队列或维护账目不符')
    status=dict(s.split('=',1) for s in (root/'block-trace.status').read_text().splitlines())
    require(status['完整']=='1' and not status['原因'],'旁观轨迹没有完整收尾')
    _,count=read_trace(root/'block-trace.csv');clock_offset(lines)
    require(count==int(status['批次']),'轨迹批次与实际收尾记录不符')
    first=actual['实际模型成本及决策'][0];n=first['决策']['轮次']
    sources={int(r['源Zone']) for r in actual['初态']}
    require({int(c['Zone']) for c in first['成本'] if c['安全']=='1'}==sources,
        '必须同时存在两个原合格且安全的候选，不以单候选冒充模型权衡')
    hot=actual['初态'][1]['源Zone']
    blocks=[fields(s,'V1影子候选：') for s in lines if 'V1影子候选：' in s]
    rates=[fields(s.split('，原因=',1)[0],'近期ETA对照：') for s in lines if '近期ETA对照：' in s]
    b=next(r for r in blocks if r['轮次']==n and r['Zone']==hot)
    rate=next(r for r in rates if r['轮次']==n and r['Zone']==hot)
    require(int(b['十秒真实增量'])>0 and rate['近期可知']=='1','缺少旧热度及真实近期同块证据')
    if scenario=='burst-stopped':
        require(int(rate['近期增量'])==0 and int(b['当前RC'])==928,'没有形成近期停读对照')
    else:require(int(rate['近期增量'])>0,'持续热读场景缺少真实近期增长')
    changes=[]
    for decision in actual['实际模型成本及决策']:
        safe=[r for r in decision['成本'] if r['安全']=='1']
        if not safe:continue
        greedy=min(safe,key=lambda r:(int(r['有效']),int(r['Zone'])))
        selected=int(decision['决策']['实际Zone'])
        if selected and selected!=int(greedy['Zone']):
            chosen=next(r for r in safe if int(r['Zone'])==selected)
            changes.append(dict(轮次=int(decision['决策']['轮次']),实际Zone=selected,
                同轮Greedy=int(greedy['Zone']),当前额外有效字节=int(chosen['有效'])-int(greedy['有效']),
                预计RR抵扣=int(chosen['RR抵扣']),预计ETA纳秒=int(chosen['ETA'])))
    actual.update(策略=mode,实际评分核验=scores,采样批次=count,相对同轮Greedy改选=changes,
        原始证据SHA256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()})
    return actual


def analyze_pair(root,scenario):
    root=Path(root);arms=[]
    for mode in ('greedy','expected'):
        out=root/(scenario+'-'+mode)
        state=json.loads((out/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'阶段归档或客机退出未完成')
        arms.append(analyze_arm(out/scenario,mode,scenario))
    require((root/(scenario+'-greedy')/scenario/'software.before.log').read_bytes()==
        (root/(scenario+'-expected')/scenario/'software.before.log').read_bytes(),'两臂库、程序或包装器身份不同')
    result=compare(*arms)
    result['模型版本']='期望RR实验V1：概率乘预计留存，直接净复制/回收排序；泊松假设未校准'
    result['结论限制']='真实固定25秒维护对照，不是YCSB；允许负收益，末态空闲量不同，不能以少一次RR替代总成本结论'
    return result
