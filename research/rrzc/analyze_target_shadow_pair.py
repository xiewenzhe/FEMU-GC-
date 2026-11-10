"""将同次双目标建议关联实际尝试与目标；不把部分观察当成反事实净收益。"""
from collections import Counter, defaultdict
from bisect import bisect_left, bisect_right
import csv
import json

from analyze_target_shadow import parse
from analyze_placement_trace import rows
import analyze_survivor_shadow as previous
from ycsb_af import require

MODES=('G2',previous.MODE)


def link(raw, report):
    native=parse((raw/'run.zenfs.log').read_text())
    require(bool(native['逐次']), '完整业务没有目标备选观察，不能发布本轮采证')
    require(not native['格式化失败'], '本轮有目标观察格式化失败')
    attempts=defaultdict(list)
    for r in rows(raw/'组成.bin'):
        if r[3]==9:
            attempts[r[5]].append(dict(事件=r[0],纳秒=r[1],标签=r[7],新写=bool(r[8])))
    targets=defaultdict(list)
    for row in report['组成']['目标']:targets[row['对象']].append(row)
    gates={x['尝试事件']:x for x in report['旧G2影子']['逐次']}
    linked=[];seen=set()
    for row in native['逐次']:
        owner=int(row['对象']);begin=int(row['开始纳秒']);end=int(row['完成纳秒'])
        possible=attempts[owner];times=[a['纳秒'] for a in possible]
        candidates=possible[bisect_left(times,begin):bisect_right(times,end)]
        candidates=[a for a in candidates if a['标签']==int(row['标签']) and a['新写']==(row['路径']=='新写')]
        require(len(candidates)==1,'双目标建议无法唯一关联本次尝试')
        attempt=candidates[0];seq=attempt['事件']
        require(seq not in seen,'同次尝试重复双目标记录');seen.add(seq)
        after=[a['事件'] for a in possible if a['事件']>seq]
        stop=min(after) if after else 1<<64
        selected=[t for t in targets[owner] if seq<t['事件']<stop]
        actual=[t for t in selected if t['类型']=='实际目标']
        require(len(actual)==1,'成功业务的实际目标未唯一关联')
        actual=actual[0];pool=any(t['类型']=='池目标' for t in selected)
        if pool:
            require(int(row['协同建议动作'])!=0 and actual['Zone']==int(row['协同建议Zone']) and
                actual['代际']==int(row['协同代际']),'实际入池身份与原建议不符')
        ordinary=row['普通部分区扫描建议']=='1'
        comparable=ordinary and row['协同字段可知']=='1'
        gate=gates.get(seq,{})
        linked.append(dict(**row,尝试事件=seq,文件编号=actual['文件编号'],实际目标事件=actual['事件'],
            实际Zone=actual['Zone'],实际代际=actual['代际'],实际入池=pool,
            本次历史拒绝=gate.get('拒绝协同',False),
            普通建议与实际同身份=ordinary and actual['Zone']==int(row['普通建议Zone']) and
                actual['代际']==int(row['普通代际']),
            两个已观察候选字段可比较=comparable,
            普通候选当前有效更少=comparable and int(row['普通有效'])<int(row['协同有效']),
            当前有效差普通减协同=int(row['普通有效'])-int(row['协同有效']) if comparable else None,
            实际目标H字节=actual['当前组成']['已知H字节'],
            实际目标W字节=actual['当前组成']['已知Flush字节'],
            实际目标未分类SST字节=actual['当前组成']['未分类SST'],
            实际目标引用一致=actual['引用数值一致'],实际目标H已确认=actual['H状态已确认']))
    require(all(g['尝试事件'] in seen for g in gates.values() if g['拒绝协同']),
            '历史拒绝未全部采集双目标观察')
    counts=Counter(r['路径'] for r in linked)
    return dict(逐次=linked,观察数=len(linked),路径计数=dict(counts),
        完整观察数=sum(r['观察完整']=='1' for r in linked),
        有已观察普通部分区建议数=sum(r['普通部分区扫描建议']=='1' for r in linked),
        两个已观察候选字段可比较数=sum(r['两个已观察候选字段可比较'] for r in linked),
        普通候选当前有效更少数=sum(r['普通候选当前有效更少'] for r in linked),
        历史拒绝数=sum(r['本次历史拒绝'] for r in linked),
        边界='观察中的普通部分区建议，不含真实后续令牌/Empty/预留/Finish；有效量差不是本次迁移节省')


def analyze(raw, mode):
    report=previous.analyze(raw,mode)
    report['同次目标影子']=link(raw,report)
    return report


def finish(output):
    result=previous.finish(output)
    reports={mode:json.loads((output/mode/'业务核验.json').read_text()) for mode in MODES}
    all_rows=[dict(模式=mode,**r) for mode in MODES for r in reports[mode]['同次目标影子']['逐次']]
    summary={mode:{k:v for k,v in reports[mode]['同次目标影子'].items() if k!='逐次'} for mode in MODES}
    folder=output/'配对分析'
    for name,selected in [('同次建议与实际目标.csv',all_rows),
                          ('历史拒绝双目标比较.csv',[r for r in all_rows if r['本次历史拒绝']])]:
        fields=list(dict.fromkeys(k for r in all_rows for k in r))
        with (folder/name).open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fields);writer.writeheader();writer.writerows(selected)
    (folder/'同次目标影子核验.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return result
