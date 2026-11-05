"""用真实迁移链区分省复制和推迟复制；读取同一K=1、原资格的两类受控结果。"""
import json
from pathlib import Path
from analyze_colocation_cases import (table,frames,normalize_blocks,logical_requests,business_log,compare_repeats,MiB)
from analyze_colocation_choice import decisions
from audit_colocation_transactions import audit

CASES=('retire','live')


def arm(root,scenario,mode,selection_reader=decisions):
    assert (root/'wrapper.exit').read_text().strip()=='0'
    text=(root/'zenfs.log').read_text();run=(root/'run.log').read_text()
    assert f'抵扣校准完成：{scenario}' in run and '[ERROR]' not in text
    assert '候选范围配置：版本=1，合法=1，扩大=0' in text
    assert '受控GC批次检查：启用=1，原启动水平=20%' in text
    assert '受控GC紧急检查：' not in text
    points=frames(root);p=points['prepared'];end=points['business']
    assert p['有效']==6019*MiB+MiB//2 and p['空闲']==1536*MiB and p['满区可回收']==252*MiB+MiB//2
    assert end['有效']==6274*MiB+(0 if scenario=='retire' else 3*MiB//2)
    assert all(r['采样Open']<=8 and r['采样Active']<=12 and r['会话历史最高RC']<=1024 for r in points.values())
    assert all(x['RC']==0 for x in normalize_blocks(root,'prepared'))
    req=logical_requests(root)
    assert sum(x[2]=='READ' for x in req)==2236
    assert [int(x[4]) for x in req if x[2]=='CHECK']==[1,2,3,4]
    assert (root/'受控GC.ack').read_text().strip()=='4'
    tx=audit(root);choice=selection_reader(root)
    assert choice and all(x['字段']['执行Greedy']==int(mode=='greedy') for x in choice)
    assert len(choice[0]['候选'])==2,'首轮无真实竞争，不评价模型'
    layout=table(root/'business.extents.csv');h=next(x for x in layout if x['文件']=='/joint/H.sst')
    blocks=[x for x in table(root/'business.blocks.csv') if x['Zone']==h['Zone']]
    delta={k:end[k]-p[k] for k in ('实际NAND读取','计入RC读取','自身刷新豁免读取',
        '其他块搬迁计数读取','阈值触发事件','被阻止读取')}
    return dict(通过=True,情形=scenario,选择器=mode,维护=tx['维护账目'],逐笔事务=tx['事务'],
        按文件复制=tx['按文件复制'],逐轮选择=choice,实际改选次数=sum(x['改选'] for x in choice),
        空间=points,业务设备增量=delta,H终点Zone=int(h['Zone']),H终点最大RC=max(int(x['RC']) for x in blocks))


def initial_equal(a,b):
    return (normalize_blocks(a,'prepared')==normalize_blocks(b,'prepared') and
        table(a/'prepared.extents.csv')==table(b/'prepared.extents.csv') and
        table(a/'prepared.zones.csv')[3:]==table(b/'prepared.zones.csv')[3:])


def analyze(output):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None
    base=output/state['标签'];groups={};pairs={}
    for scenario in CASES:
        paths={}
        for mode in ('greedy','model'):
            roots=[base/f'{scenario}-k1-{mode}-r{n}' for n in (1,2)]
            reports=[arm(r,scenario,mode) for r in roots];repeat=compare_repeats(*roots)
            assert repeat['通过'],str(repeat);paths[mode]=roots[0]
            groups[f'{scenario}-{mode}']=dict(各轮=reports,重复=repeat)
        assert logical_requests(paths['greedy'])==logical_requests(paths['model'])
        assert initial_equal(paths['greedy'],paths['model']),'同情形的选择器起点不同'
        a,b=(groups[f'{scenario}-{mode}']['各轮'][0] for mode in ('greedy','model'))
        assert a['空间']['business']['有效']==b['空间']['business']['有效']
        pairs[scenario]=dict(总迁移差字节=b['维护']['总迁移字节']-a['维护']['总迁移字节'],
            D复制差字节=sum(b['按文件复制'].get('/joint/D.sst',{}).get(k,0)-
                a['按文件复制'].get('/joint/D.sst',{}).get(k,0) for k in ('ZC字节','RR字节')),
            终态空闲差字节=b['空间']['business']['空闲']-a['空间']['business']['空闲'],
            终态满区可回收差字节=b['空间']['business']['满区可回收']-a['空间']['business']['满区可回收'])
    assert initial_equal(base/'retire-k1-greedy-r1',base/'live-k1-greedy-r1')
    result=dict(通过=True,各组=groups,对照=pairs,
        边界='显式D退役的受控ZenFS因果实验；标签不提供D的未来；原模型冻结；不是正常并发或应用性能')
    assert not (output/'抵扣校准核验.json').exists()
    (output/'抵扣校准核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# RR抵扣与真实复制校准','','|情形|选区|ZC/RR|总复制MiB|D复制MiB|终点空闲MiB|满区可回收MiB|',
        '|---|---|---:|---:|---:|---:|---:|']
    for name,g in groups.items():
        r=g['各轮'][0];m=r['维护'];d=r['按文件复制'].get('/joint/D.sst',{});p=r['空间']['business']
        lines.append(f'|{r["情形"]}|{r["选择器"]}|{m["ZC次数"]}/{m["RR次数"]}|'
            f'{m["总迁移字节"]/MiB:g}|{(d.get("ZC字节",0)+d.get("RR字节",0))/MiB:g}|'
            f'{p["空闲"]/MiB:g}|{p["满区可回收"]/MiB:g}|')
    lines+=['',result['边界'],'','正式对照必须连同后续256MiB写入、额外1024读、空间及RC终态解释；'
        '不能只比较第一次检查后的RR数或空闲量。']
    (output/'抵扣校准报告.md').write_text('\n'.join(lines)+'\n');return result
