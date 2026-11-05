"""同候选范围的空间、旧时间一致与第二版边界模型YCSB验收；原入口不变。"""
from collections import Counter
import json
from pathlib import Path
from analyze_joint_opportunity import fields,require
from analyze_window_ycsb import analyze_arm as shared
from analyze_deadline import audit as old_audit,audit_forecasts,choose
from analyze_terminal import audit as terminal_audit
from replay_terminal import inputs,choose_v2

MODES=('greedy','space','model')
NAMES=dict(greedy='同扩展候选空间控制（非stock Greedy）',space='旧时间一致RR-aware',model='第二版边界RR-aware')

def analyze_arm(root,stage,mode):
    root=Path(root);terminal=mode=='model'
    out=shared(root,stage,mode,expected_rr=True,strict_rr=True,space_plan=True,
        space_plan_version=3,same_scope=True,joint_time=True,terminal_version=2 if terminal else None)
    require((root/'时序.txt').read_text()==
        '时序开关=1\n近期窗口秒=2至4\n预测支撑上限秒=10\n边界版本=2\n边界开关='+str(int(terminal))+'\n',
        '实际时序或边界配置不符')
    lines=(root/'zenfs.run.log').read_text().splitlines()
    plans=terminal_audit(lines,version=2) if terminal else old_audit(lines)
    predictions=audit_forecasts(lines,time_consistent=True)
    business=json.loads((root/'strict.business.json').read_text())
    verified=json.loads((root/'strict.after-verify.json').read_text())
    for r in (business,verified):
        require(r['版本']==r['启用']==1 and 0<=r['历史最高RC']<=1024,'严格RR未启用或RC越界')
        require(r['实际NAND读取']==r['计入RC读取']+r['自身刷新豁免读取'],'严格读取账目不闭合')
        require(r['其他块搬迁计数读取']<=r['计入RC读取'],'其他块搬迁读取未计数')
    require(all(verified[k]>=business[k] for k in business),'独立校验后计数回退')
    if stage=='short' and mode=='greedy':
        require(business['阈值触发事件']>0 and out['RR次数']>0 and business['历史最高RC']==1024,
            '空间控制没有覆盖RR，不能作为有效RR研究对照')
    # 同一轮冻结输入的反事实只说明排序贡献，不冒充另一次闭环真实实验。
    needs,rows=inputs(lines);decisions=[]
    for p in plans:
        n=p['轮次'];current=rows.get(n,[])
        old=choose(current,needs[n])[1]
        no_rr=[dict(r,p=0,credit=0,space=0) for r in current]
        zero=choose_v2(no_rr,needs[n]) if terminal else None
        zero_zone=(zero['zone'] if zero['found'] else None) if terminal else choose(no_rr,needs[n])[1]
        decisions.append(dict(轮次=n,实际Zone=p['实际Zone'],旧模型Zone=old,
            同轮RR项置零Zone=zero_zone))
    zones=json.loads((root/'zones.final.json').read_text())['zone_list']
    out.update(策略名称=NAMES[mode],时序预测核验=predictions,空间方案=plans,选区贡献=decisions,
        正抵扣记录数=sum(p['RR抵扣']>0 for p in predictions),严格RR业务账目=business,严格RR校验后账目=verified,
        最终硬件Zone状态=dict(Counter(z['state'] for z in zones)),
        业务末态硬件可写剩余字节=sum(max(0,z['slba']+z['cap']-z['wp'])*512 for z in zones if z['state']!='FULL'),
        末态口径='装载、Run及Cleanup之后，独立验证之前；硬件尾部不等于普通可分配空间',
        贡献口径='旧模型及RR置零选择来自同轮冻结输入重算，不是另一闭环运行或真实节省量')
    return out

def compare_three(root,stage):
    root=Path(root);arms={};folders=[]
    for mode in MODES:
        p=root/(stage+'-'+mode);state=json.loads((p/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'归档或关机未完成')
        folders.append(p/(stage+'-'+mode));arms[mode]=analyze_arm(folders[-1],stage,mode)
    first=arms['greedy']
    require(all(arms[m][k]==first[k] for m in MODES for k in ('请求SHA256','最终值SHA256')),
            '三组请求或最终值不同')
    for name in ('workload.properties','software.before.log'):
        require(len({(p/name).read_bytes() for p in folders})==1,'三组条件不一致：'+name)
    metrics=('RR次数','ZC次数','总迁移字节','总成功ZoneReset','推导物理Block擦除次数','业务末态硬件可写剩余字节')
    return dict(三组验收通过=True,阶段=stage,模式=arms,
        相对空间控制差值={m:{k:arms[m][k]-first[k] for k in metrics} for m in MODES[1:]},
        修正版相对旧模型差值={k:arms['model'][k]-arms['space'][k] for k in metrics},
        边界='同库、同请求、同扩展候选及共享安全路径；空间对照关闭RR与留存管线，非单参数消融；4GiB单轮不证明稳态或因果收益')
