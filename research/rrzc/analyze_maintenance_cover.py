"""核验真实执行的新评分、共同观测Greedy及逐笔维护链；不预设正收益。"""
import json
from pathlib import Path
from analyze_all_blocks_dbbench import fields
from analyze_colocation_cases import business_log,compare_repeats,logical_requests,MiB
from analyze_colocation_choice import arm as high_arm
from analyze_colocation_credit import arm as low_arm,initial_equal
from replay_maintenance_cover import snapshots,evaluate

CASES=('choice','retire','live')


def decisions(root,binary):
    plans={};needs={};suggestions={}
    for line in business_log(root).splitlines():
        if '空间覆盖候选：' in line:
            r=fields(line,'空间覆盖候选：');n=int(r['轮次'])
            row={k:int(r[k]) for k in ('首区','当前计划复制','计划外预计RR复制','评分总复制','静态回收','计划外当前有效量')}
            row.update(覆盖=bool(int(r['覆盖'])),计划区=[int(z) for z in r['计划区'].split('/')])
            plans.setdefault(n,[]).append(row)
        if '空间覆盖需求：' in line:
            r={k:int(v) for k,v in fields(line,'空间覆盖需求：').items()}
            assert r['轮次'] not in needs;needs[r['轮次']]=r
        if '空间覆盖决策：' in line:
            r={k:int(v) for k,v in fields(line,'空间覆盖决策：').items()}
            assert r['轮次'] not in suggestions;suggestions[r['轮次']]=r
    result=[]
    for r in evaluate(binary,snapshots(root)):
        n=r['轮次'];new=r['新建议'];actual=r['原实际决策'];d=r['需求']
        assert needs[n]==dict(轮次=n,可知=int(new['目标可知']),退出目标=new['退出目标'],
            退出缺口=new['退出缺口'],分配等待=d['分配等待'],启动水平=d['目标百分比'])
        assert plans.get(n,[])==new['计划'],'设备评分与冻结宿主核重算不符'
        assert suggestions[n]==dict(轮次=n,存在=int(new['存在']),Zone=new['Zone'],
            输入非法=int(new['输入非法']),仅空间进展=int(new['仅空间进展']),实际只搬首区=1)
        assert not new['输入非法']
        pool=[x for x in r['候选'] if x['安全'] and 0<x['有效']<x['容量'] and x['RR复制']<=x['有效']]
        greedy=min(pool,key=lambda x:(x['有效'],x['Zone'])) if pool else None
        assert actual['模型存在']==int(new['存在']) and actual['Greedy存在']==int(greedy is not None)
        if new['存在']:assert actual['模型Zone']==new['Zone']
        assert actual['GreedyZone']==(greedy['Zone'] if greedy else 0)
        assert actual['实际存在']==int(bool(pool))
        if pool:assert actual['实际Zone']==(greedy['Zone'] if actual['执行Greedy'] else new['Zone'])
        # 独立按源候选重算复制账目，已在计划内的Zone不重复列入RR。
        candidates={x['Zone']:x for x in pool}
        for p in new['计划']:
            assert len(set(p['计划区']))==len(p['计划区']) and p['首区']==p['计划区'][0]
            selected=[candidates[z] for z in p['计划区']]
            assert p['当前计划复制']==sum(x['有效'] for x in selected)
            assert p['静态回收']==sum(x['容量']-x['有效'] for x in selected)
            if not new['仅空间进展']:
                assert p['计划外预计RR复制']==sum(x['RR复制'] if x['风险可知'] else 0
                    for z,x in candidates.items() if z not in p['计划区'])
            assert p['计划外当前有效量']==sum(x['有效'] for z,x in candidates.items() if z not in p['计划区'])
        result.append(dict(字段=actual,候选=pool,改选=bool(pool) and actual['实际Zone']!=actual['GreedyZone'],
            空间需求=new,候选方案=new['计划']))
    assert len(result)==len(needs)==len(suggestions),'轮次字段缺失或多余'
    return result


def arm(root,scenario,mode,binary):
    reader=lambda path:decisions(path,binary)
    return high_arm(root,1,mode,reader) if scenario=='choice' else low_arm(root,scenario,mode,reader)


def analyze(output,binary):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
    assert state['当前客机PID'] is None
    base=output/state['标签'];groups={};pairs={}
    for scenario in CASES:
        first={}
        for mode in ('greedy','model'):
            paths=[base/f'{scenario}-k1-{mode}-r{i}' for i in (1,2)]
            reports=[arm(p,scenario,mode,binary) for p in paths]
            repeat=compare_repeats(*paths);assert repeat['通过'],str(repeat)
            groups[f'{scenario}-{mode}']=dict(各轮=reports,重复=repeat);first[mode]=paths[0]
        assert initial_equal(first['greedy'],first['model'])
        assert logical_requests(first['greedy'])==logical_requests(first['model'])
        g,m=[groups[f'{scenario}-{mode}']['各轮'][0] for mode in ('greedy','model')]
        a,b=g['空间']['business'],m['空间']['business'];assert a['有效']==b['有效']
        pairs[scenario]=dict(总复制差字节=m['维护']['总迁移字节']-g['维护']['总迁移字节'],
            终点空闲差字节=b['空闲']-a['空闲'],终点可回收差字节=b['满区可回收']-a['满区可回收'],
            模型改选次数=m['实际改选次数'])
    result=dict(通过=True,各组=groups,对照=pairs,
        边界='共同观测、原候选及受控检查门；实际文件路径验证，不是RocksDB/YCSB性能或长期稳态结论')
    path=output/'同空间目标核验.json';assert not path.exists()
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 同空间目标真实运行结果','',result['边界'],'',
        '|情形|选择|ZC/RR|总复制MiB|改选次数|终点空闲MiB|终点Active|','|---|---|---:|---:|---:|---:|---:|']
    for g in groups.values():
        r=g['各轮'][0];m=r['维护'];s=r['空间']['business']
        lines.append(f'|{r.get("情形","choice")}|{r["选择器"]}|{m["ZC次数"]}/{m["RR次数"]}|'
            f'{m["总迁移字节"]/MiB:g}|{r["实际改选次数"]}|{s["空闲"]/MiB:g}|{s["采样Active"]}|')
    lines+=['','各组均先验收本轮Greedy两次重复，再允许模型两次重复。'
        '计划及RR概率是当时估计；收益必须按实际复制、终点空间和后续维护解释。']
    (output/'同空间目标报告.md').write_text('\n'.join(lines)+'\n');return result
