#!/usr/bin/env python3
"""跨两轮四臂Greedy复核；相同二进制是必要条件，不强制事件或统计结果相同。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_reproducibility import (initial,request_comparison,accounting,collect,compare_stream,
    ROCK_FIELDS,MARKERS,decisions,transactions)
from analyze_calibration import timing


def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def ratio(values):
    if not values or min(values)<=0:raise ValueError('比较值缺失或非正，不能伪造比值')
    return max(values)/min(values)


def same_conditions(a,b):
    # 二进制相同仍可能具有不同实验环境，不能合并为同配置重复。
    keys=('初始装载键数','RC策略','真实块计数准备','内部随机性控制','前台固定全局顺序',
        '固定逻辑输入','共享单后台线程','每次写后排空后台','逐请求跟踪','设备稀疏事件',
        'Flush同步分段','GC批次检查门','最大写缓冲数')
    if any(k not in a or k not in b for k in keys):raise ValueError('重复配置证据缺失，不合并统计')
    changed=[k for k in keys if a[k]!=b[k]]
    if bool(a.get('辅助目录tmpfs诊断',False))!=bool(b.get('辅助目录tmpfs诊断',False)):
        changed.append('辅助目录tmpfs诊断')
    for key,default in (('目标分配稀疏诊断',False),('实际业务批次数',24),('受控空间请求确认',False),('共同观测',False),('打开文件线程数',16),('设备命令诊断',False),('FTL诊断身份',None)):
        if a.get(key,default)!=b.get(key,default):changed.append(key)
    if changed:raise ValueError('实验条件不同，不合并为同配置重复：'+','.join(changed))


def analyze(previous,current,output):
    if previous.resolve()==current.resolve():raise ValueError('两轮指向同一结果目录，不能冒称独立重复')
    same_conditions(load(previous/'预注册计划.json'),load(current/'预注册计划.json'))
    rows=[];sources={};identities=[]
    for order,root in enumerate((previous,current),1):
        status=load(root/'状态.json');archive=load(root/'阶段归档.json')
        if not (archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and status['当前客机PID'] is None):
            raise ValueError('只比较已成功归档且退出的实验')
        source=root/status['标签'];sources[order]=source
        identity=load(root/'校准客户端身份.json');identities.append(identity)
        for tag in ('g1','g2'):
            arm=source/tag
            if not load(arm/'起始核验.json')['通过']:raise ValueError('臂起点未通过')
            m=accounting(arm)
            if not m['复制日志与维护汇总相同']:raise ValueError('维护账目不闭合')
            rows.append(dict(轮=order,臂=tag,请求窗口秒=timing(arm)['请求首尾秒'],
                ZC次数=m['ZC次数'],RR次数=m['RR次数'],总迁移字节=m['总迁移字节'],
                结束空闲字节=load(arm/'space.business.json')['空闲字节'],
                结束有效字节=load(arm/'space.business.json')['有效字节']))
    if identities[0]!=identities[1]:raise ValueError('客户端或ZenFS对象身份改变，不合并为同构建重复')
    checks={};evolution={}
    reference=sources[1]/'g1'
    controlled=bool(load(previous/'预注册计划.json').get('受控空间请求确认'))
    baseline=collect(reference) if controlled else None
    def rank_rows(root):
        return [dict(轮次=r['轮次'],成本=sorted(r['成本']),选中=r['选中'],Greedy排名=r['Greedy排名'])
            for r in decisions(root)]
    for order,source in sources.items():
        for tag in ('g1','g2'):
            arm=source/tag
            if arm==reference:continue
            checks[str(order)+'/'+tag]=dict(初始状态=initial(reference,arm),请求=request_comparison(reference,arm))
            if controlled:
                current_events=collect(arm)
                comparisons={}
                for kind in ROCK_FIELDS:
                    comparisons['RocksDB/'+kind]=compare_stream(*[[e for e in events[0] if e['事件']==kind]
                        for events in (baseline,current_events)])
                for marker in MARKERS:
                    comparisons['ZenFS/'+marker[:-1]]=compare_stream(*[[e for e in events[1] if e['事件']==marker[:-1]]
                        for events in (baseline,current_events)])
                transaction=transactions(arm)
                evolution[str(order)+'/'+tag]=dict(各类首差=comparisons,
                    所有已记录类别序列相同=all(c['相同'] for c in comparisons.values()),
                    成本资格及Victim相同=rank_rows(reference)==rank_rows(arm),
                    全部Zone终态相同=load(reference/'zones.business.json')==load(arm/'zones.business.json'),
                    完整空间终态相同=load(reference/'space.business.json')==load(arm/'space.business.json'),
                    维护事务闭合=not transaction['未闭合'] and
                        all(t['复制到提交再源Reset'] for t in transaction['完成维护']),
                    说明='只比较已记录语义字段，非完整NAND逐读同步；不同墙钟本身不算行为差异')
    complete_inputs=all(c['请求'].get('已记录') and c['请求'].get('逻辑提交相同') and
        c['请求'].get('完整成功请求数')==[1376256,1376256] for c in checks.values())
    semantic_keys=('zones.before.json','space.before.json','装载-0.sst.csv','装载-0.extent.csv',
        '布局-0.sst.csv','布局-0.extent.csv','全部物理块身份','全部物理块RC','全部物理块近期增量及统计标志')
    same_start=all(all(c['初始状态'][k]['相同'] for k in semantic_keys) for c in checks.values())
    time_ratio=ratio([r['请求窗口秒'] for r in rows]);copy_ratio=ratio([r['总迁移字节'] for r in rows])
    summary=dict(四条Greedy运行=rows,软件身份相同=True,逐请求全部一致=complete_inputs,
        已采集起点语义状态一致=same_start,请求窗口最大最小比=time_ratio,迁移最大最小比=copy_ratio,
        工程预检通过=complete_inputs and same_start and time_ratio<=1.05 and copy_ratio<=1.05,
        门槛=1.05,边界='四次工程诊断，不构成显著性；逻辑输入字节可能含时间或会话元数据，不能称完整内存检查点相同。逐区终态与事件交错另报。')
    if controlled:
        summary['受控已记录演化一致']=all(all(c[k] for k in ('所有已记录类别序列相同',
            '成本资格及Victim相同','全部Zone终态相同','完整空间终态相同','维护事务闭合')) for c in evolution.values())
        summary['受控跨轮验收通过']=summary['工程预检通过'] and summary['受控已记录演化一致']
    output.mkdir(parents=True,exist_ok=False)
    save(output/'四次重复核验.json',summary);save(output/'跨轮起点与请求.json',checks)
    if controlled:save(output/'跨轮行为首差.json',evolution)
    with (output/'四次Greedy指标.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    manifest={str(root/name):hashlib.sha256((root/name).read_bytes()).hexdigest()
        for root in (previous,current) for name in ('状态.json','阶段归档.json','预注册计划.json','校准客户端身份.json')}
    manifest[str(Path(__file__).resolve())]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(output/'来源SHA256.json',manifest)
    (output/'分析源码.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous',type=Path);parser.add_argument('current',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();analyze(args.previous,args.current,args.output)
