"""四组合受控实验：区分放置、实际选区和总复制，禁止用RR次数替代收益。"""
import json
from pathlib import Path
from fractions import Fraction
from analyze_colocation_cases import (table,frames,normalize_blocks,logical_requests,
    business_log,compare_repeats,MiB)
from analyze_all_blocks_dbbench import fields
from audit_colocation_transactions import audit


def decisions(root):
    rows={};actual=[]
    for line in business_log(root).splitlines():
        if '单位回收成本：' in line:
            r=fields(line,'单位回收成本：');n=int(r['轮次']);z=int(r['Zone'])
            item={k:int(r[k]) for k in ('Zone','有效','回收','RR抵扣','净复制')}
            assert item['回收']==128*MiB-item['有效'] and item['净复制']==item['有效']-item['RR抵扣']
            rows.setdefault(n,{})[z]=item
        if '共同观测实际决策：' in line:
            r={k:int(v) for k,v in fields(line,'共同观测实际决策：').items()}
            pool=list(rows.get(r['轮次'],{}).values())
            if not pool:
                assert r['模型存在']==r['Greedy存在']==r['实际存在']==0,'有实际选择却缺少可选成本'
                actual.append(dict(字段=r,候选=[],改选=False));continue
            best=min(pool,key=lambda x:(Fraction(x['净复制'],x['回收']),x['有效'],x['Zone']))
            greedy=min(pool,key=lambda x:(x['有效'],x['Zone']))
            assert r['模型存在']==r['Greedy存在']==r['实际存在']==1
            assert r['模型Zone']==best['Zone'] and r['GreedyZone']==greedy['Zone']
            assert r['实际Zone']==(r['GreedyZone'] if r['执行Greedy'] else r['模型Zone'])
            actual.append(dict(字段=r,候选=pool,改选=r['实际Zone']!=r['GreedyZone']))
    return actual


def arm(root,k,mode,selection_reader=decisions):
    assert (root/'wrapper.exit').read_text().strip()=='0'
    log=(root/'zenfs.log').read_text();run=(root/'run.log').read_text()
    assert f'多候选四组合验证完成：K={k}' in run and '[ERROR]' not in log
    assert '受控GC批次检查：启用=1，原启动水平=20%' in log
    assert '候选范围配置：版本=1，合法=1，扩大=0' in log
    assert '受控GC紧急检查：' not in log
    assert all(x['RC']==0 for x in normalize_blocks(root,'prepared'))
    p=frames(root);assert p['prepared']['有效']==7008*MiB and p['prepared']['空闲']==800*MiB
    assert p['business']['有效']==6808*MiB
    assert all(r['采样Open']<=8 and r['采样Active']<=12 and r['会话历史最高RC']<=1024 for r in p.values())
    tx=audit(root);choices=selection_reader(root);assert choices,'没有真实维护选择'
    assert all(r['字段']['执行Greedy']==int(mode=='greedy') for r in choices)
    assert [int(r[4]) for r in logical_requests(root) if r[2]=='CHECK']==[1,2,3]
    assert sum(r[2]=='READ' for r in logical_requests(root))==1212
    layout={r['文件']:int(r['Zone']) for r in table(root/'prepared.extents.csv')}
    h,w,c=[layout['/joint/'+name+'.sst'] for name in ('H','W','C')]
    assert (h==w and h!=c) if k else (h!=w and h==c)
    return dict(通过=True,K=k,选择器=mode,维护=tx['维护账目'],逐笔事务=tx['事务'],
        按文件复制=tx['按文件复制'],逐轮选择=choices,实际改选次数=sum(x['改选'] for x in choices),
        空间=p,布局=layout,起点只核验范围='数据区逐块身份/相对代际/RC/窗口/标志及布局；不声称元数据时钟一致')


def analyze(output):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None
    source=output/state['标签'];groups={};reference=None
    for k in (0,1):
        for mode in ('greedy','model'):
            tags=[f'choice-k{k}-{mode}-r{r}' for r in (1,2)]
            proofs=[arm(source/tag,k,mode) for tag in tags]
            repeated=compare_repeats(*(source/tag for tag in tags))
            assert repeated['通过'],str(repeated)
            requests=logical_requests(source/tags[0])
            if reference is None:reference=requests
            assert reference==requests,'四组合逻辑输入不同'
            groups[f'k{k}-{mode}']=dict(各轮=proofs,重复=repeated)
        a,b=(source/f'choice-k{k}-{mode}-r1' for mode in ('greedy','model'))
        assert normalize_blocks(a,'prepared')==normalize_blocks(b,'prepared')
        assert table(a/'prepared.extents.csv')==table(b/'prepared.extents.csv')
        assert table(a/'prepared.zones.csv')[3:]==table(b/'prepared.zones.csv')[3:]
    result=dict(通过=True,各组=groups,边界='显式受控文件、原候选、固定历史节奏和检查边界；不是RocksDB/YCSB性能；不预设正收益')
    (output/'四组合核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 多候选四组合实际结果','','|放置|选区|ZC/RR|总迁移MiB|改选次数|终点空闲MiB|终点Active|',
        '|---|---|---:|---:|---:|---:|---:|']
    for key,g in groups.items():
        r=g['各轮'][0];a=r['维护'];s=r['空间']['business']
        lines.append(f'|K={r["K"]}|{r["选择器"]}|{a["ZC次数"]}/{a["RR次数"]}|'
            f'{a["总迁移字节"]/MiB:.3f}|{r["实际改选次数"]}|{s["空闲"]/MiB:.3f}|{s["采样Active"]}|')
    lines+=['',result['边界'],'','先比较同K的模型和Greedy，再比较相同选区器下的放置差异。'
        '相同RC与请求不保证采样纳秒、预测概率及评分完全相同；实际决定与复制账目另行逐笔核验。',
        '终态空间或Active不同应报告负担，不将业务窗差异直接称作长期净收益。']
    (output/'阶段四报告.md').write_text('\n'.join(lines)+'\n');return result
