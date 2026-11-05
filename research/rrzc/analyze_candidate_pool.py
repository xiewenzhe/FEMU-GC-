"""严格RR候选范围三组受控验收；不是YCSB或稳态性能结论。"""
import hashlib
import json
from pathlib import Path
import re
from analyze_joint_opportunity import fields,require,table,model_decisions
from analyze_window_credit import audit_scores
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS

MODES=('original-greedy','expanded-greedy','expanded-expected')
CASES=('opportunity','low-yield','stopped')


def qualification(row,expanded):
    v,c,g,t=(int(row[k]) for k in ('有效字节','容量字节','无效比例','原比例门槛'))
    require(c>0 and 0<=v<=c and g==100-100*v//c and row['满区']=='1','候选容量或原比例计算不符')
    original=g>t and v>0
    actual=0<v<c if expanded else original
    require(row['原比例资格']==str(int(original)) and row['扩大']==str(int(expanded)) and
            row['实际比例资格']==str(int(actual)),'实际候选范围不符合登记模式')
    return actual


def strict_stats(row):
    require(row['版本']==row['启用']==1 and 0<=row['历史最高RC']<=1024,'严格RR配置或RC越界')
    require(row['实际NAND读取']==row['计入RC读取']+row['自身刷新豁免读取'] and
            row['其他块搬迁计数读取']<=row['计入RC读取'],'严格读取账目不闭合')


def analyze_arm(root,mode,scenario):
    root=Path(root);require(mode in MODES and scenario in CASES,'未知受控模式或场景')
    expanded=mode!='original-greedy';expected=mode=='expanded-expected'
    for name in ('wrapper.exit','run.exit','verify.exit'):
        require((root/name).read_text().strip()=='0','阶段失败：'+name)
    a=(root/'software.before.log').read_text();b=(root/'software.after.log').read_text()
    require(a and a==b and all(s.endswith(': OK') for s in a.splitlines()),'运行前后软件身份不一致')
    zones=json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(zones)==32 and all(z['state']=='EMPTY' and z['slba']==z['wp'] and
            z['cap']*512==128<<20 for z in zones),'初始NAND不是全空4GiB设备')
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('[ERROR]' not in raw and '状态=失败' not in raw,'业务日志存在错误')
    require('严格RR主机配置：启用=1' in raw,'没有确认严格RR主机配置')
    pool='expanded' if expanded else 'original'
    scores=audit_scores(lines,'window' if expected else 'greedy',persistent=expected,
        expected_rr=expected,candidate_pool=pool)
    initial=table(root/'initial.csv');reads=table(root/'reads.csv')
    require(len(initial)==2 and [int(r['有效字节']) for r in initial]==
            [4<<20,(120 if scenario=='low-yield' else 5)<<20],'实际载荷与受控登记不同')
    require([int(r['RC']) for r in initial]==[0,928] and
            all(r['代际']=='0' and r['空闲百分比']=='17' for r in initial),'初始RC、代际或空间不同')
    require(int(initial[1]['十秒真实增量'])==(0 if scenario=='stopped' else 928),'初始真实十秒增量不同')
    count=0 if scenario=='stopped' else 128
    require(len(reads)==count and all(int(r['序号'])==n and
            int(r['计划纳秒'])==n*10**9//16 for n,r in enumerate(reads)),'实际读取计划不同')
    release=[i for i,s in enumerate(lines) if '联合维护释放：' in s]
    end=[i for i,s in enumerate(lines) if '联合维护观察结束：' in s]
    require(len(release)==len(end)==1 and release[0]<end[0],'观察边界缺失')
    require(not ZC_STATS.search('\n'.join(lines[:release[0]])) and
            not RR_STATS.search('\n'.join(lines[:release[0]])),'准备阶段发生提前维护')
    body=lines[release[0]+1:end[0]];decisions=model_decisions(body)
    require(decisions,'没有真实ZC成本决策')
    # 每轮用紧邻的原始比例记录核对准入；不把扩大后的资格冒称原资格。
    current={};rows=[]
    for line in lines:
        if 'GC 检查：' in line:current={}
        elif '候选范围资格：' in line:
            r=fields(line,'候选范围资格：');qualification(r,expanded);current[r['Zone']]=r;rows.append(r)
        elif '统一成本：' in line:
            r=fields(line,'统一成本：');require(r['Zone'] in current and
                current[r['Zone']]['实际比例资格']=='1','评分中混入未准入候选')
    safe={r['Zone'] for r in decisions[0]['成本'] if r['安全']=='1'}
    hot=initial[1]['源Zone'];cold=initial[0]['源Zone']
    require(cold in safe and (hot in safe)==(expanded or scenario!='low-yield'),
            '首轮没有形成登记的候选范围差异')
    if not expected:require(all(int(c['RR抵扣'])==0 for d in decisions for c in d['成本']),'Greedy出现RR收益抵扣')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',raw))=={'20'},'GC启动条件改变')
    require(not any(t in raw for t in ('空间紧急回收结果：','公共单区恢复维护结果：')),'混入应急选区')
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    copies=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr)
    require(all(r['复制成功']=='1' for r in copies) and
            sum(int(r['有效字节']) for r in copies)==moved,'逐文件与维护完成复制账目不一致')
    resets=[tuple(map(int,m.groups())) for s in lines if
        (m:=re.search(r'Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，',s))]
    require(all(c==b+1 for _,b,c in resets) and len(resets)>=sum(r[2] for r in zc+rr),'Reset代际或完成账目不符')
    business=json.loads((root/'strict.business.json').read_text())
    after=json.loads((root/'strict.after-verify.json').read_text());strict_stats(business);strict_stats(after)
    require(all(after[k]>=business[k] for k in business),'校验阶段计数回退')
    require('只读校验通过：存活候选完整载荷' in (root/'verify.stdout.log').read_text() and
            '[ERROR]' not in (root/'zenfs.verify.log').read_text(),'独立载荷校验失败')
    changes=[]
    for d in decisions:
        safe=[c for c in d['成本'] if c['安全']=='1'];chosen=d['决策']['实际Zone']
        if safe and chosen!=min(safe,key=lambda c:(int(c['有效']),int(c['Zone'])))['Zone']:
            changes.append(d['决策'])
    return dict(验收通过=True,模式=mode,场景=scenario,初态=initial,
        请求计划=[(r['序号'],r['计划纳秒']) for r in reads],候选资格=rows,
        首轮实际Zone=decisions[0]['决策']['实际Zone'],相对同轮Greedy改选=changes,
        ZC次数=sum(r[2] for r in zc),RR次数=sum(r[2] for r in rr),
        ZC复制字节=sum(r[1] for r in zc),RR复制字节=sum(r[1] for r in rr),总复制字节=moved,
        总成功ZoneReset=len(resets),推导Block擦除=len(resets)*128,
        严格RR业务账目=business,严格RR校验后账目=after,独立评分核验=scores,
        读取最大调度落后纳秒=max((int(r['实际开始纳秒'])-int(r['计划纳秒']) for r in reads),default=0),
        最终Zone状态=json.loads((root/'zones.final.json').read_text())['zone_list'],
        日志SHA256=hashlib.sha256(raw.encode()).hexdigest(),
        边界='真实25秒受控观察，不是YCSB；严格RR可能使后续请求落后于计划，完整记录不丢请求。校验维护另记；擦除为Reset乘128推导')


def compare_three(arms):
    require(set(arms)==set(MODES),'三组不完整')
    a=arms[MODES[0]]
    require(all(r['场景']==a['场景'] and r['初态']==a['初态'] and r['请求计划']==a['请求计划']
                for r in arms.values()),'三组初始映射、RC或计划不同')
    return dict(三组验收通过=True,模式=arms,比较={
        title:{k:arms[right][k]-arms[left][k] for k in ('RR次数','ZC次数','总复制字节','总成功ZoneReset')}
        for title,left,right in (('扩大范围影响',MODES[0],MODES[1]),
            ('同范围评分影响',MODES[1],MODES[2]),('机制相对原基线',MODES[0],MODES[2]))},
        边界='同库同程序、各臂真实重建NAND；没有相同内存快照，不强求维护次数相同或预设正收益')
