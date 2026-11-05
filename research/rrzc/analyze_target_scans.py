"""解析实际目标扫描；占用失败只报告忙，不猜测未取得独占权的字段。"""
import re
from analyze_reproducibility import fields


def parse(line):
    head,details=line.split('，候选=',1)
    meta={k:int(v) for k,v in fields(head,'迁移目标扫描：').items()}
    rows=[]
    for body in re.findall(r'\[([^\]]+)\]',details):
        row={}
        for token in body.split('，'):
            key,value=token.split('=',1);row[key]=int(value)
        assert row['取得'] in (0,1)
        if row['取得']==0:
            assert set(row)=={'Zone','取得'},'忙区不应读取无锁状态'
            row['资格解释']='原扫描Acquire失败，内部容量和寿命未读取'
        elif row['有效']==0:row['资格解释']='无有效数据，不属于部分目标匹配'
        elif row['满']:row['资格解释']='已满'
        elif row['类别']!=1:row['资格解释']='SST内容类别不兼容'
        elif row['剩余']<meta['最小容量']:row['资格解释']='剩余容量不足'
        else:row['资格解释']='按当前采样字段满足匹配资格'
        rows.append(row)
    assert rows and len({r['Zone'] for r in rows})==len(rows),'扫描目标缺失或重复'
    best=100;winner=(1<<64)-1
    for row in rows:
        if row['资格解释']=='按当前采样字段满足匹配资格' and row['匹配差']<=best:
            best=row['匹配差'];winner=row['Zone']
    return dict(头=meta,候选=rows,按采样字段重放目标=winner,
        与日志匹配目标一致=winner==meta['匹配Zone'],
        边界='只读重放已记录字段；有效量可被其他线程递减，非原子快照。SST内容隔离须在原实验配置确认开启。')


def scans(text):
    return [dict(日志行=n,原文=line,**parse(line)) for n,line in enumerate(text.splitlines(),1)
        if '迁移目标扫描：' in line]
