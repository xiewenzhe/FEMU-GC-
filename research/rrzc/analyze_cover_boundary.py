"""固定候选大小的六配置真实账目核验；不把预期正收益作为验收条件。"""
import json
from pathlib import Path
from analyze_maintenance_cover import decisions
from analyze_colocation_cases import table,frames,normalize_blocks,logical_requests,compare_repeats,MiB
from analyze_colocation_credit import initial_equal
from audit_colocation_transactions import audit
from replay_maintenance_cover import evaluate,snapshots

CASES=tuple(f'{pressure}-{life}' for pressure in ('low','high') for life in ('early','late','live'))


def check_schedule(requests,life):
    business=[r for r in requests if r[1]=='业务']
    checks=[(n,int(r[4])) for n,r in enumerate(business) if r[2]=='CHECK']
    assert [x[1] for x in checks]==[1,2,3,4]
    assert sum(r[2]=='READ' for r in business)==2236
    assert sum(int(r[5]) for r in business if r[2]=='APPEND')==256*MiB
    deletes=[n for n,r in enumerate(business) if r[2]=='DELETE' and r[3]=='/joint/D.sst']
    assert len(deletes)==(0 if life=='live' else 1)
    if life=='early':assert checks[0][0]<deletes[0]<next(n for n,r in enumerate(business) if n>checks[0][0] and r[2]=='READ')
    if life=='late':assert checks[2][0]<deletes[0]<checks[3][0]


def arm(root,scenario,mode,binary):
    assert scenario in CASES
    assert (root/'wrapper.exit').read_text().strip()=='0'
    log=(root/'zenfs.log').read_text();run=(root/'run.log').read_text()
    assert f'边界实验完成：{scenario}' in run and '[ERROR]' not in log
    assert '候选范围配置：版本=1，合法=1，扩大=0' in log
    assert '受控GC批次检查：启用=1，原启动水平=20%' in log
    assert '受控GC紧急检查：' not in log
    high=scenario.startswith('high-');life=scenario.split('-')[1]
    points=frames(root);p=points['prepared'];end=points['business']
    expected=6019*MiB+MiB//2+(512*MiB if high else 0)
    assert p['有效']==expected and p['空闲']==(1024 if high else 1536)*MiB
    assert p['满区可回收']==252*MiB+MiB//2
    assert end['有效']==expected+256*MiB-(0 if life=='live' else 3*MiB//2)
    assert all(x['采样Open']<=8 and x['采样Active']<=12 and x['会话历史最高RC']<=1024 for x in points.values())
    assert all(x['RC']==0 for x in normalize_blocks(root,'prepared'))
    requests=logical_requests(root);check_schedule(requests,life)
    assert (root/'受控GC.ack').read_text().strip()=='4'
    selected=decisions(root,binary);assert selected and len(selected[0]['候选'])==2
    assert all(d['字段']['执行Greedy']==int(mode=='greedy') for d in selected)
    candidates={x['Zone']:x for x in selected[0]['候选']}
    assert {x['有效'] for x in candidates.values()}=={2*MiB,3*MiB//2}
    h=next(x for x in table(root/'seed.extents.csv') if x['文件']=='/joint/H.sst')
    d=next(x for x in table(root/'seed.extents.csv') if x['文件']=='/joint/D.sst')
    assert int(h['字节'])==2*MiB and int(d['字节'])==3*MiB//2
    blocks=table(root/'seed.blocks.csv')
    assert max(int(x['RC']) for x in blocks if x['Zone']==h['Zone'])==900
    assert max(int(x['RC']) for x in blocks if x['Zone']==d['Zone'])==0
    # 只替换字节需求做离线归因，不把它冒充另一次实际执行。
    record=snapshots(root)[0];counterfactual=[]
    for free in (1536*MiB,1024*MiB):
        changed=dict(record,需求=dict(record['需求'],空闲=free,非空闲=7808*MiB-free))
        counterfactual.append(evaluate(binary,[changed])[0]['新建议'])
    tx=audit(root)
    delta={k:end[k]-p[k] for k in ('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')}
    assert delta['被阻止读取']==0
    return dict(通过=True,情形=scenario,选择器=mode,维护=tx['维护账目'],逐笔事务=tx['事务'],
        按文件复制=tx['按文件复制'],逐轮选择=selected,实际改选次数=sum(x['改选'] for x in selected),
        空间=points,业务设备增量=delta,同候选仅换空间的离线建议=counterfactual)


def analyze(output,binary):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None
    base=output/state['标签'];groups={};pairs={};seeds={}
    for scenario in CASES:
        modes={}
        for mode in ('greedy','model'):
            paths=[base/f'{scenario}-k1-{mode}-r{i}' for i in (1,2)]
            reports=[arm(p,scenario,mode,binary) for p in paths]
            repeated=compare_repeats(*paths);assert repeated['通过'],str(repeated)
            groups[f'{scenario}-{mode}']=dict(各轮=reports,重复=repeated);modes[mode]=paths[0]
        assert initial_equal(modes['greedy'],modes['model'])
        assert logical_requests(modes['greedy'])==logical_requests(modes['model'])
        assert normalize_blocks(modes['greedy'],'seed')==normalize_blocks(modes['model'],'seed')
        g,m=[groups[f'{scenario}-{mode}']['各轮'][0] for mode in ('greedy','model')]
        a,b=g['空间']['business'],m['空间']['business'];assert a['有效']==b['有效']
        pairs[scenario]=dict(总复制差字节=m['维护']['总迁移字节']-g['维护']['总迁移字节'],
            终态空闲差字节=b['空闲']-a['空闲'],终态可回收差字节=b['满区可回收']-a['满区可回收'],
            模型改选次数=m['实际改选次数'])
        seeds[scenario]=modes['greedy']
    # 同压力三种未来的输入在首轮选区前相同；不要求两种压力的填充布局相同。
    for pressure in ('low','high'):
        reference=seeds[f'{pressure}-early']
        for life in ('late','live'):
            other=seeds[f'{pressure}-{life}']
            assert initial_equal(reference,other)
            assert normalize_blocks(reference,'seed')==normalize_blocks(other,'seed')
            prefix=lambda p:logical_requests(p)[:next(i for i,r in enumerate(logical_requests(p)) if r[2]=='CHECK' and r[4]=='1')+1]
            assert prefix(reference)==prefix(other)
    result=dict(通过=True,各组=groups,对照=pairs,
        边界='固定H2/D1.5MiB、两压力三寿命；未重编冻结选择器；受控文件与检查门，不是正常并发性能')
    target=output/'边界核验.json';assert not target.exists()
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 固定候选的空间压力与寿命边界','',result['边界'],'',
        '|情形|选择器|ZC/RR|总复制MiB|D复制MiB|改选|末端空闲MiB|末端Active|',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for group in groups.values():
        r=group['各轮'][0];m=r['维护'];e=r['空间']['business'];d=r['按文件复制'].get('/joint/D.sst',{})
        lines.append(f'|{r["情形"]}|{r["选择器"]}|{m["ZC次数"]}/{m["RR次数"]}|'
            f'{m["总迁移字节"]/MiB:g}|{(d.get("ZC字节",0)+d.get("RR字节",0))/MiB:g}|'
            f'{r["实际改选次数"]}|{e["空闲"]/MiB:g}|{e["采样Active"]}|')
    (output/'边界结果.md').write_text('\n'.join(lines)+'\n');return result
