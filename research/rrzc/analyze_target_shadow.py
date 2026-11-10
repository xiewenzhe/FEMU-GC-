"""解析只读目标比较记录；扫描建议不代表最终分配成功或反事实净收益。"""
import argparse
import csv
import json
from pathlib import Path


def parse(text):
    records=[];ids=set();unknown=[]
    for line_number,line in enumerate(text.splitlines(),1):
        if '目标备选影子未知：' in line:
            unknown.append(dict(行=line_number,原文=line));continue
        if '目标备选影子：' not in line:continue
        fields={}
        for field in line.split('目标备选影子：',1)[1].split('，'):
            key,value=field.split('=',1)
            if key in fields:raise ValueError('目标比较字段重复')
            fields[key]=value
        identifier=int(fields['决策'])
        if identifier in ids:raise ValueError('同进程决策序号重复')
        ids.add(identifier)
        if int(fields['开始纳秒'])>int(fields['完成纳秒']):raise ValueError('观测时间倒退')
        for field in ('目标未独占','未申请令牌','未提交'):
            if fields[field]!='1':raise ValueError('只读影子边界不符')
        if fields['路径'] not in ('新写','必要迁移'):raise ValueError('申请路径非法')
        if fields['路径']=='新写' and fields['普通容量下限']!='0':raise ValueError('新写容量下限不是原扫描值')
        partial=fields['普通部分区扫描建议']=='1'
        if partial != ('普通建议Zone' in fields):raise ValueError('普通建议身份不闭合')
        if fields['观察完整']=='1' and fields['未观察区数']!='0':raise ValueError('未知区却标完整')
        records.append(dict(原生行=line_number,**fields))
    return dict(逐次=records,格式化失败=unknown,边界='仅记录观察下扫描建议；没有最终分配或反事实业务')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('不覆盖已有派生数据')
    result=parse(args.log.read_text())
    if not result['逐次']:raise ValueError('日志没有实际目标备选观察')
    args.output.mkdir(parents=True)
    fields=list(dict.fromkeys(k for row in result['逐次'] for k in row))
    with (args.output/'同次目标建议.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields);writer.writeheader();writer.writerows(result['逐次'])
    (args.output/'解析核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
