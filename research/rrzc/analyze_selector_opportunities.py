"""回溯冻结Greedy的候选、同代际首次后继及静态计划盲区；不推算反事实收益。"""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import stamp
from replay_maintenance_cover import evaluate
from ycsb_af import digest,require

ZONE=128<<20


def timeline(lines):
    epochs=defaultdict(int);events=defaultdict(list);decisions={};costs={};details={};files=defaultdict(dict)
    for number,line in enumerate(lines,1):
        if 'V1影子文件：' in line:
            x=fields(line,'V1影子文件：');key=(int(x['轮次']),int(x['Zone']))
            fid=int(x['文件编号']);require(fid not in files[key],'候选文件重复')
            files[key][fid]=int(x['区内有效字节'])
        for marker,name in (('全块代表门控：','代表'),('时间一致证据：','时间'),('时序读预测：','预测')):
            if marker in line:
                x=fields(line,marker);key=(int(x['轮次']),int(x['Zone']))
                details.setdefault(key,{})[name]=dict(行=number,字段=x)
        if '时序成本：' in line:
            x={k:int(v) for k,v in fields(line,'时序成本：').items()};key=(x['轮次'],x['Zone'])
            require(key not in costs,'成本行重复')
            costs[key]=dict(字段=x,行=number,Reset序数=epochs[x['Zone']])
        if '共同观测实际决策：' in line:
            x={k:int(v) for k,v in fields(line,'共同观测实际决策：').items()}
            require(x['轮次'] not in decisions,'决策轮次重复')
            decisions[x['轮次']]=dict(行=number,纳秒=stamp(line),字段=x)
        rr=re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节',line)
        zc=fields(line,'简单双因素实际集合：') if '简单双因素实际集合：' in line else None
        if rr or (zc and zc['实际选中']=='1'):
            z=int(rr[1] if rr else zc['Zone'])
            events[z].append(dict(类型='RR' if rr else 'ZC',行=number,纳秒=stamp(line),
                Reset序数=epochs[z],原始行=line,有效字节=int(rr[3]) if rr else None,
                达阈值块数量=int(rr[2]) if rr else None,实际触发块编号=None))
        if 'Zone 重置诊断：' in line:
            x=fields(line,'Zone 重置诊断：');require(x['状态']=='成功','Reset失败')
            before,after=map(int,x['主机代际'].split('/'));require(after==before+1,'主机代际不递增')
            z=int(x['Zone']);events[z].append(dict(类型='Reset',行=number,纳秒=stamp(line),
                Reset序数=epochs[z],原始行=line,有效字节=None,主机前代际=before,主机后代际=after))
            epochs[z]+=1
    return decisions,costs,details,files,events


def next_event(events,line,epoch):
    # 首次Reset即截止，不能跨过地址复用去认领后来一代的RR。
    event=next((x for x in events if x['行']>line),None)
    if event is not None:require(event['Reset序数']==epoch,'代际断点缺失')
    return event


def analyze_arm(root,tag,binary):
    p=root/'逐臂采证'/tag;report=json.loads((root/(tag+'-核验.json')).read_text())
    require(report['通过'] and (p/'wrapper.exit').read_text().strip()=='0','来源未通过')
    raw=p/'run.zenfs.log';lines=raw.read_text().splitlines()
    require('协同尾部配置：版本=1，执行=0，观测=1' in raw.read_text(),'不分析开启尾部的混杂轨迹')
    decisions,costs,details,files,events=timeline(lines);observations=[];blind=[];scenarios=[]
    active=[d for d in report['逐轮选择'] if d['候选']]
    for d in report['逐轮选择']:
        n=d['字段']['轮次'];actual=decisions[n]
        require(actual['字段']==d['字段'] and d['字段']['执行Greedy']==1,'实际决策不是共同Greedy')
        for c in d['候选']:
            key=(n,c['Zone']);require(costs[key]['字段']==c,'缓存候选与原日志不同')
        if len(d['候选'])>1:
            plans=d['候选方案'];members={frozenset(x['计划区']) for x in plans}
            if len(plans)==len(d['候选']) and len(members)==1:
                shared=next(iter(members));all_ids={c['Zone'] for c in d['候选']}
                require(shared==all_ids,'共同计划集合未覆盖全部当前候选')
                blind.append(n)
                # 明确标注的敏感性输入：给某区最大可能抵扣，检验计划结构，绝非真实预测。
                for target in d['候选']:
                    candidates=[dict(c,RR复制=c['有效'] if c['Zone']==target['Zone'] else 0,
                                     风险可知=1) for c in d['候选']]
                    need=d['空间需求'];scenarios.append(dict(轮次=n,目标=target['Zone'],候选=candidates,
                        需求=dict(空闲=0,非空闲=0,分配等待=0,目标百分比=20),原实际决策=d['字段']))
                    # 从已记录的原需求恢复真实free/non_free，不能凭空间覆盖结果反推。
                    found=[fields(line,'时序方案需求：') for line in lines
                           if '时序方案需求：' in line and int(fields(line,'时序方案需求：')['轮次'])==n]
                    require(len(found)==1,'原空间需求不唯一')
                    scenarios[-1]['需求']={k:int(v) for k,v in found[0].items()}
        selected=next((c for c in d['候选'] if c['Zone']==d['字段']['实际Zone']),None)
        for c in d['候选']:
            if c is selected:continue
            key=(n,c['Zone']);base=costs[key];event=next_event(events[c['Zone']],actual['行'],base['Reset序数'])
            row=dict(轮次=n,候选Zone=c['Zone']//ZONE,实际Zone=selected['Zone']//ZONE,
                候选有效字节=c['有效'],实际有效字节=selected['有效'],额外当前复制字节=c['有效']-selected['有效'],
                ETA纳秒=c['ETA'],RR预计复制字节=c['RR复制'],决策行=actual['行'],成本行=base['行'],
                本日志Reset序数=base['Reset序数'],后继=event['类型'] if event else '业务结束截尾',
                后继间隔纳秒=event['纳秒']-actual['纳秒'] if event else None,
                后继事件=event,同集合计划=n in blind,诊断=details.get(key,{}))
            if event and event['类型']=='RR':
                # 事务分析器多加一行派生窗前缀；用原始选择行再次核实行号与身份。
                tasks=[x for x in report['事务'] if x['开始行']==event['行']+1]
                require(len(tasks)==1 and tasks[0]['原始选择行']==event['原始行'],'RR事务与原日志断链')
                copied=defaultdict(int)
                for x in tasks[0]['复制']:copied[int(x['字段']['文件编号'])]+=int(x['字段']['有效字节'])
                require(sum(copied.values())==event['有效字节']==tasks[0]['迁移字节'],'RR复制账目不闭合')
                require(sum(files[key].values())==c['有效'],'原候选文件覆盖不完整')
                row.update(候选文件字节=files[key],实际RR文件字节=dict(copied),
                    原文件同字节全部进入RR=dict(copied)==files[key],
                    后续RR复制字节=tasks[0]['迁移字节'],
                    两笔账面余量字节=tasks[0]['迁移字节']-row['额外当前复制字节'])
            observations.append(row)
    sensitivity=evaluate(binary,scenarios) if scenarios else []
    for x in sensitivity:
        require(x['新建议']['Zone']==x['原实际决策']['GreedyZone'],
                '最大抵扣改变了共同计划首区，须重新分析盲区')
    return dict(来源=str(p),原日志SHA256=digest(raw),核验SHA256=digest(root/(tag+'-核验.json')),
        总决策轮数=len(decisions),有候选轮数=len(active),多候选轮数=sum(len(d['候选'])>1 for d in active),
        同全集计划轮次=blind,正RR金额候选数=sum(c['RR复制']>0 for d in active for c in d['候选']),
        未选候选后继=dict(Counter(r['后继'] for r in observations)),候选与后继=observations,
        最大金额结构敏感性=dict(合成=True,测试数=len(sensitivity),改变选择数=0,
            边界='在真实候选及空间状态上注入上限金额，仅检验冻结评分结构，不是实际预测或设备收益'))


def analyze(root,output):
    require(not output.exists(),'不覆盖原分析')
    state=json.loads((root/'状态.json').read_text());archive=json.loads((root/'阶段归档.json').read_text())
    require(state.get('完整实验完成') and state['当前客机PID'] is None and archive['完整']
        and archive['业务成功'] and not archive['客机仍存活'],'来源未完整退出')
    require(json.loads((root/'关闭重复核验.json').read_text())['通过'],'Greedy重复未通过')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不符')
    frozen=root/'源码/宿主评分核';here=Path(__file__).resolve().parent
    sources=('rrzc_maintenance_cover.h','rrzc_unit_cost_selector.h','rrzc_joint_time_selector.h','rrzc_window_credit_selector.h')
    identity={name:digest(frozen/name) for name in sources}
    require(all(identity[n]==digest(here/'model'/n) for n in sources),'当前核与冻结核不同，不能静默替换')
    with tempfile.TemporaryDirectory(prefix='rrzc-opportunity-') as tmp:
        binary=Path(tmp)/'reader'
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-I'+str(frozen),
            str(here/'tests/maintenance_cover_replay.cc'),'-o',str(binary)],check=True)
        reports={tag:analyze_arm(root,tag,binary) for tag in ('p1','p2')}
    result=dict(通过=True,来源=str(root),归档SHA256=state['镜像处理']['archive_sha256'],
        冻结核身份=identity,各臂=reports,分析器SHA256=digest(Path(__file__)),
        边界='原Greedy轨迹的同代际首次后继；随后RR只是潜在联合机会，不是改选后可避免复制的因果估计；不启动客机，不改模型')
    output.mkdir(parents=True);(output/'机会与盲区.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(Path(__file__),output/'分析源码.py')
    rows=[dict(臂=t,**{k:v for k,v in x.items() if not isinstance(v,(dict,list))})
          for t,r in reports.items() for x in r['候选与后继']]
    with (output/'全部未选候选.csv').open('x',newline='',encoding='utf-8-sig') as f:
        keys=list(dict.fromkeys(k for r in rows for k in r));w=csv.DictWriter(f,keys);w.writeheader();w.writerows(rows)
    print(json.dumps({t:{k:v for k,v in r.items() if k!='候选与后继'} for t,r in reports.items()},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();analyze(a.source,a.output)
