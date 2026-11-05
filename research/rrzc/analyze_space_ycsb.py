"""三组真实YCSB验收；区分空间控制收益与RR预测收益，原始证据不改写。"""
from collections import Counter
import json
from pathlib import Path
from analyze_window_ycsb import analyze_arm as shared,digest
from analyze_joint_opportunity import fields,require
from analyze_space_plan import audit_plans

MODES=('greedy','space','model')

def analyze_arm(root,stage,mode,*,version=1):
    root=Path(root)
    out=shared(root,stage,mode,expected_rr=True,strict_rr=True,space_plan=True,space_plan_version=version)
    lines=(root/'zenfs.run.log').read_text().splitlines()
    if version==3 and mode!='greedy':
        from analyze_deadline import audit,audit_forecasts
        plans=audit(lines);predictions=audit_forecasts(lines)
        out['时序预测核验']=predictions
        out['正抵扣记录数']=sum(x['RR抵扣']>0 for x in predictions)
    else:plans=audit_plans(lines) if mode!='greedy' else []
    c3=json.loads((root/'strict.business.json').read_text())
    verified=json.loads((root/'strict.after-verify.json').read_text())
    for r in (c3,verified):
        require(r['版本']==r['启用']==1 and 0<=r['历史最高RC']<=1024,'严格RR未启用或RC越界')
        require(r['实际NAND读取']==r['计入RC读取']+r['自身刷新豁免读取'],'严格读取账目不闭合')
        require(r['其他块搬迁计数读取']<=r['计入RC读取'],'其他块迁移没有计数')
    require(all(verified[k]>=c3[k] for k in c3),'校验阶段严格账目回退')
    if stage=='short' and mode=='greedy':
        require(c3['阈值触发事件']>0 and out['RR次数']>0 and c3['历史最高RC']==1024,
            '原Greedy没有覆盖RR，不能验收为RR研究对照')
    final=json.loads((root/'zones.final.json').read_text())['zone_list']
    free=sum(max(0,z['slba']+z['cap']-z['wp'])*512 for z in final if z['state']!='FULL')
    costs={}
    for line in lines:
        tag='时序成本：' if version==3 and mode!='greedy' else '统一成本：'
        if tag in line:
            r=fields(line,tag);costs.setdefault(int(r['轮次']),[]).append(r)
    changes=[]
    for p in plans:
        safe=[r for r in costs.get(p['轮次'],[]) if r['安全']=='1']
        if p['实际Zone'] is None or not safe:continue
        greedy=min(safe,key=lambda r:(int(r['有效']),int(r['Zone'])))
        if int(greedy['Zone'])!=p['实际Zone']:
            actual=next(r for r in safe if int(r['Zone'])==p['实际Zone'])
            changes.append(dict(轮次=p['轮次'],实际Zone=p['实际Zone'],同候选Greedy=int(greedy['Zone']),
                当前额外复制=int(actual['有效'])-int(greedy['有效']),ETA纳秒=int(actual['ETA']),
                预计RR复制=int(actual['RR复制' if version==3 else 'RR抵扣'])))
    out.update(空间方案版本=version,严格RR业务账目=c3,严格RR校验后账目=verified,空间方案=plans,
        本轮不选区次数=sum(p['实际Zone'] is None and p['缺口字节']==0 for p in plans),
        仅空间进展次数=sum(p['仅进展'] for p in plans),相对同候选Greedy改选=changes,
        最终硬件Zone状态=dict(Counter(z['state'] for z in final)),业务末态硬件可写剩余字节=free,
        末态口径='业务及Cleanup之后、独立校验之前；硬件尾部不等于普通可分配空间，更不代表有效数据量',
        模型='空间缺口下的方案总复制；未选区预计RR复制加当前首区及补充ZC复制，RR预计回收另列；预测假设未校准')
    return out

def compare_three(root,stage,*,version=1):
    root=Path(root);arms={};folders=[]
    for mode in MODES:
        p=root/(stage+'-'+mode);state=json.loads((p/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'归档或关机未完成')
        folders.append(p/(stage+'-'+mode));arms[mode]=analyze_arm(folders[-1],stage,mode,version=version)
    first=arms['greedy']
    for mode in MODES[1:]:
        require(all(arms[mode][k]==first[k] for k in ('请求SHA256','最终值SHA256')),'同负载请求或最终值不一致')
    for name in ('workload.properties','software.before.log'):
        require(len({(p/name).read_bytes() for p in folders})==1,'不同实验条件：'+name)
    metrics=('RR次数','ZC次数','总迁移字节','总成功ZoneReset','推导物理Block擦除次数','业务末态硬件可写剩余字节')
    return dict(三组验收通过=True,阶段=stage,模式=arms,
        相对原Greedy差值={m:{k:arms[m][k]-first[k] for k in metrics} for m in MODES[1:]},
        RR模型相对仅空间修正={k:arms['model'][k]-arms['space'][k] for k in metrics},
        边界='同库、同请求、三臂新NAND真实装载；单轮及4GiB功能规模不证明稳态；空间控制不等于RR预测收益，终态空间必须一起看')
