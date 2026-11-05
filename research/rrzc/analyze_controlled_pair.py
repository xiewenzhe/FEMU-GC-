"""核验共同受控程序下的Greedy与现有模型；单配对不冒称正式性能收益。"""
import csv
import json
from pathlib import Path

from analyze_all_blocks_dbbench import arm
from analyze_calibration import timing
from analyze_reproducibility import (initial,request_comparison,accounting,transactions,collect,
    compare_stream,ROCK_FIELDS,MARKERS)
from analyze_target_repeat import analyze as target_check
from cache_config_guard import check as cache_check
from analyze_io_trace import compare as compare_io

SEMANTIC=('zones.before.json','space.before.json','装载-0.sst.csv','装载-0.extent.csv',
    '布局-0.sst.csv','布局-0.extent.csv','全部物理块身份','全部物理块RC','全部物理块近期增量及统计标志')


def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def reference_guard(reference,current):
    """模型运行前只核验新Greedy；不要求模型改选后的维护事件相同。"""
    assert reference.resolve()!=current.resolve(),'不能用同一份结果自证复现'
    req=request_comparison(reference,current);start=initial(reference,current)
    a,b=collect(reference),collect(current);comparisons={}
    for kind in ROCK_FIELDS:
        comparisons['RocksDB/'+kind]=compare_stream(*[[e for e in events[0] if e['事件']==kind] for events in (a,b)])
    for marker in MARKERS:
        comparisons['ZenFS/'+marker[:-1]]=compare_stream(*[[e for e in events[1] if e['事件']==marker[:-1]] for events in (a,b)])
    io=compare_io(reference,current)
    counters=('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')
    deltas=[]
    for root in (reference,current):
        before=load(root/'真实准备读取.json')['设备后'];after=load(root/'strict.business.json')
        assert after['启用']==1 and after['历史最高RC']<=1024
        delta={k:after[k]-before[k] for k in counters}
        assert all(v>=0 for v in delta.values()),'累计数倒退，不能跨会话比较'
        deltas.append(delta)
    ok=(req['逻辑提交相同'] and req['完整成功请求数']==[1376256]*2 and
        all(start[k]['相同'] for k in SEMANTIC) and all(v['相同'] for v in comparisons.values()) and
        load(reference/'zones.business.json')==load(current/'zones.business.json') and
        load(reference/'space.business.json')==load(current/'space.business.json') and
        accounting(reference)==accounting(current) and io['设备摘要相同'] and
        io['稀疏反馈首差'] is None and deltas[0]==deltas[1])
    return dict(通过=ok,请求=req,起点=start,各类首差=comparisons,
        设备逐请求核验=io,分类设备增量=deltas,
        边界='只要求Greedy复现基线；模型改选后的合理状态分叉另行归因。设备增量窗口含重新打开，非完整NAND快照。')


def analyze(output,dest):
    plan=load(output/'预注册计划.json');status=load(output/'状态.json');archive=load(output/'阶段归档.json')
    assert plan['模型配对'] and plan['每臂策略']==dict(g1='greedy',g2='blocks')
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and status['当前客机PID'] is None
    source=output/status['标签'];dest.mkdir(exist_ok=False)
    # 模型使用时序决策日志，不能拿只识别统一决策的Greedy首差工具误报零轮。
    target_check(source,dest/'请求与事务',24,compare_allocations=False)
    roots={tag:source/tag for tag in ('g1','g2')}
    semantic=('zones.before.json','space.before.json','装载-0.sst.csv','装载-0.extent.csv',
        '布局-0.sst.csv','布局-0.extent.csv','全部物理块身份','全部物理块RC','全部物理块近期增量及统计标志')
    state=initial(roots['g1'],roots['g2'])
    assert all(state[k]['相同'] for k in semantic) and state['逻辑输入回放.json']['相同'],'两臂起点或输入不同'
    assert (roots['g1']/'公共参数.txt').read_bytes()==(roots['g2']/'公共参数.txt').read_bytes(),'公共参数不同'
    data={}
    for tag,mode in plan['每臂策略'].items():
        root=roots[tag]
        cache=cache_check((root/'run.rocksdb.log').read_text())
        measured=arm(root,mode,load_root=source/'source',records=917504,
            all_blocks_observation=bool(plan.get('共同观测')))
        if plan.get('RR空间替代计价'):
            assert all('单位回收评分' in r and (r['实际Zone'] is None or
                r['单位回收评分']['空间替代账目']) for r in measured['选区轮次'] if r['阶段']=='业务'), '实际日志缺少V2空间替代成本'
        ledger=accounting(root);transaction=transactions(root)
        assert ledger['复制日志与维护汇总相同'] and ledger['映射提交失败数']==0
        assert not transaction['未闭合'] and all(t['复制到提交再源Reset'] for t in transaction['完成维护'])
        data[tag]=dict(策略=mode,缓存=cache,计时=timing(root),维护=ledger,
            逐轮选区=measured['选区轮次'],候选明细=measured['候选明细'],API延迟=measured['API延迟'])
    donor=output.parent/(plan['复用诊断客户端']+'-results');donor_state=load(donor/'状态.json')
    if plan.get('重复验收'):
        accepted=Path(plan['重复验收']);proof=load(accepted);accepted_state=load(accepted.with_name('状态.json'))
        assert proof['验收通过'] and Path(accepted_state['本轮'])==donor,'来源没有对应四次验收'
    else:assert donor_state.get('受控跨轮验收通过') or donor_state.get('受控同步验收通过'),'来源没有受控重复验收'
    if plan.get('单位回收成本'):
        assert load(output/'新模型Greedy重复核验.json')['通过'],'新构建Greedy重复未验收'
        build=load(source/'目标采证身份.json');identity=load(output/'校准客户端身份.json')
        assert build['单位回收模型'] and build['组合对象SHA256']==identity['ZenFS对象SHA256']
        assert bool(build.get('RR空间替代计价'))==bool(plan.get('RR空间替代计价')),'空间成本模型版本不同'
        assert load(source/'宿主恢复核验.json')['通过'],'原软件恢复未通过'
    else:
        assert load(donor/'校准客户端身份.json')==load(output/'校准客户端身份.json'),'共同程序改变'
    reference=donor/donor_state['标签']/'g1';current=roots['g1']
    req=request_comparison(reference,current);start=initial(reference,current)
    original_events=collect(reference);current_events=collect(current);comparisons={}
    for kind in ROCK_FIELDS:
        comparisons['RocksDB/'+kind]=compare_stream(*[[e for e in events[0] if e['事件']==kind]
            for events in (original_events,current_events)])
    for marker in MARKERS:
        comparisons['ZenFS/'+marker[:-1]]=compare_stream(*[[e for e in events[1] if e['事件']==marker[:-1]]
            for events in (original_events,current_events)])
    baseline_ok=(req['逻辑提交相同'] and req['完整成功请求数']==[1376256]*2 and
        all(start[k]['相同'] for k in semantic) and all(c['相同'] for c in comparisons.values()) and
        load(reference/'zones.business.json')==load(current/'zones.business.json') and
        load(reference/'space.business.json')==load(current/'space.business.json'))
    if plan.get('共同观测'):
        guarded=reference_guard(reference,current)
        baseline_ok=baseline_ok and guarded['通过']
        save(dest/'Greedy设备复现核验.json',guarded)
    save(dest/'Greedy对既有基线核验.json',dict(通过=baseline_ok,请求=req,起点=start,各类首差=comparisons))
    switches=[r for r in data['g2']['逐轮选区'] if r['发生改选'] and r['阶段']=='业务']
    zero_switch=None
    if plan.get('RR空间替代计价') and not switches:
        zero_switch=reference_guard(roots['g1'],roots['g2'])
        save(dest/'零改选轨迹核验.json',zero_switch)
    metrics={t:dict(**d['维护'],请求窗口秒=d['计时']['请求首尾秒'],
        请求窗口吞吐ops=d['计时']['请求窗口吞吐ops']) for t,d in data.items()}
    changes={k:metrics['g2'][k]-metrics['g1'][k] for k in metrics['g1'] if not isinstance(metrics['g1'][k],bool)}
    observation={}
    for tag,root in roots.items():
        text=(root/'run.zenfs.log').read_text()
        observation[tag]=dict(ZenFS日志字节=len(text.encode()),逐块诊断日志行数=text.count('全块风险明细：'))
    matched=bool(plan.get('共同观测')) and all(v['逐块诊断日志行数']==0 for v in observation.values())
    summary=dict(配对起点及请求通过=True,Greedy既有基线复现通过=baseline_ok,改选次数=len(switches),
        模型版本='单位回收净复制及RR空间替代' if plan.get('RR空间替代计价') else '单位回收净复制' if plan.get('单位回收成本') else '原全块时序计划',
        指标=metrics,模型减Greedy差值=changes,观测日志=observation,
        共同观测已对齐=matched,受控收益归因前提通过=baseline_ok and matched,
        零改选轨迹复现通过=zero_switch['通过'] if zero_switch else None,
        正式收益归因通过=False,
        限制=('两臂查询和计算相同，逐块明细均关闭；须核对首次真实改选及随后维护结果，单次受控配对不能发布正常并发或统计稳定收益。'
            if matched else '额外C4查询及逐块日志未对齐，不能直接归因选区收益'))
    save(dest/'配对核验.json',summary);save(dest/'完整选区与指标.json',data);save(dest/'改选明细.json',switches)
    for tag,d in data.items():
        with (dest/(tag+'-逐轮选区.csv')).open('x') as stream:
            rows=d['逐轮选区'];keys=list(dict.fromkeys(k for row in rows for k in row)) if rows else ['阶段']
            writer=csv.DictWriter(stream,fieldnames=keys)
            writer.writeheader()
            for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    return summary
