"""旧G2只读建议与实际W历史准入的逐次对照；建议不是最终分配承诺。"""
from collections import defaultdict,deque
import csv
import json
import re

import analyze_placement_survivor as previous
from ycsb_af import require

MODE=previous.MODE


def parse_shadow(log):
    records=[]
    expression=(r'W旧策略影子：对象=(\d+)，历史=(\d+)，需要=(\d+)，可知=1，建议动作=(\d+)，'
        r'建议Zone=(\d+)，代际=(\d+)，修订=(\d+)，空闲Active=(\d+)，空闲Open=(\d+)，'
        r'原因=(.*?)，未申请令牌=1，未提交=1')
    keys=['对象','历史','需要','建议动作','建议Zone','代际','修订','空闲Active','空闲Open']
    for number,line in enumerate(log.splitlines(),1):
        found=re.search(expression,line)
        if found:
            record={key:int(value) for key,value in zip(keys,found.groups()[:9])}
            record.update(可知=True,原因=found[10],日志行=number);records.append(record)
        elif 'W旧策略影子未知：' in line:
            found=re.search(r'W旧策略影子未知：对象=(\d+)，原因=(.*?)，原门控继续=1',line)
            require(found is not None,'影子未知日志格式不符')
            records.append(dict(对象=int(found[1]),可知=False,原因=found[2],日志行=number))
    return records


def analyze(root,mode):
    report=previous.analyze(root,mode)
    records=parse_shadow((root/'run.zenfs.log').read_text())
    owners=defaultdict(deque)
    for row in records:owners[row['对象']].append(row)
    linked=[]
    for attempt in report['W历史准入']['逐次']:
        require(bool(owners[attempt['对象']]),'W申请缺少影子建议或未知记录')
        shadow=owners[attempt['对象']].popleft()
        if mode==MODE:
            require(shadow['日志行']>attempt['评估日志行'],'影子不在本次实际评估之后')
            if attempt['拒绝协同']:
                require(shadow['日志行']<attempt['拒绝日志行'],'影子不在本次拒绝之前')
        if shadow['可知']:
            require(shadow['历史'] in (0,1,2) and
                (shadow['历史']==2)==(attempt['本会话先前成功提交事件'] is not None),
                '影子历史和真实先前提交不符')
            require(shadow['建议动作'] in (0,1,2),'影子动作不合法')
            suggested=shadow['建议动作']!=0
            if mode==MODE:require(shadow['历史']==attempt['原生历史状态'],'影子和实际准入历史不符')
        else:suggested=None
        linked.append(dict(**attempt,影子可知=shadow['可知'],影子日志行=shadow['日志行'],
            原G2建议入池=suggested,原G2建议动作=shadow.get('建议动作'),
            原G2建议目标=shadow.get('建议Zone'),原G2建议代际=shadow.get('代际'),
            原G2建议原因=shadow['原因'],
            拒绝覆盖原入池建议=attempt['拒绝协同'] and suggested is True,
            拒绝但原建议已回退=attempt['拒绝协同'] and suggested is False))
    require(not any(owners.values()),'影子建议未全部匹配到实际W尝试')
    report['旧G2影子']=dict(逐次=linked,已知建议数=sum(x['影子可知'] for x in linked),
        未知建议数=sum(not x['影子可知'] for x in linked),
        拒绝覆盖原入池建议数=sum(x['拒绝覆盖原入池建议'] for x in linked),
        拒绝但原建议已回退数=sum(x['拒绝但原建议已回退'] for x in linked),
        原G2建议可入池但实际未入池数=sum(x['原G2建议入池'] is True and not x['实际入池'] for x in linked),
        边界='共享观察对应的纯Choose建议；没有实际尝试Acquire/令牌/Commit，不能称必定会入池。')
    return report


def finish(output):
    result=previous.finish(output)
    reports={mode:json.loads((output/mode/'业务核验.json').read_text()) for mode in ['G2',MODE]}
    records=[dict(模式=mode,**row) for mode in reports for row in reports[mode]['旧G2影子']['逐次']]
    if records:
        with (output/'配对分析/旧G2建议与实际逐次对照.csv').open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,list(records[0]));writer.writeheader();writer.writerows(records)
    summary={mode:{key:value for key,value in reports[mode]['旧G2影子'].items() if key!='逐次'} for mode in reports}
    (output/'配对分析/影子建议核验.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return result
