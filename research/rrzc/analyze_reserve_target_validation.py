"""独立核对补充目标资格及其真实后续ZC；不把目标观察当成实际放置。"""
from analyze_v1_shadow_stream import fields,require
from analyze_controlled_zc import ZC_STATS

def inspect(text):
    require('预留目标资格配置：启用=1，' in text,'公共目标资格开关未确认')
    candidates={};gates={};chosen=None;proof=[];admitted=0;copies=[]
    for line in text.splitlines():
        if 'GC 检查：' in line:
            candidates={};gates={};chosen=None;copies=[]
        if '预留目标资格候选：' in line:
            g=fields(line,'预留目标资格候选：');z=int(g['Zone']);gates[z]=g
            empty=int(g['空目标可用']);reserve=int(g['预留可用'])
            require(int(g['生效目标资格'])==int(bool(empty or reserve)),'补充资格布尔关系不符')
            if reserve:
                admitted+=1
                require(z!=int(g['预留Zone']) and int(g['复核稳定'])==1,'预留与源相同或观察不稳定')
                require(0<int(g['片段数']) and 0<int(g['物理上界字节'])<=int(g['预留剩余字节'])<=int(g['预留容量字节']),
                    '物理上界不适配预留尾部')
        if '简单双因素候选：' in line:
            c=fields(line,'简单双因素候选：');z=int(c['Zone']);candidates[z]=c
            require(z in gates,'缺补充资格日志')
            require(c['空目标资格']==gates[z]['生效目标资格'],'选择器未使用已记录资格')
            if gates[z]['预留可用']=='1':
                require(c['覆盖资格']=='1' and c['有效字节']==gates[z]['覆盖字节'],'源覆盖不完整')
        if '简单双因素实际集合：' in line:
            d=fields(line,'简单双因素实际集合：')
            if d.get('实际选中')=='1':chosen=int(d['Zone'])
        if 'V1后续迁移复制：' in line:
            copies.append(fields(line,'V1后续迁移复制：'))
        if (m:=ZC_STATS.search(line)) and chosen is not None:
            g=gates[chosen]
            if g['空目标可用']=='0' and g['预留可用']=='1':
                stats=tuple(map(int,m.groups()))
                proof.append(dict(源Zone=chosen,有效字节=int(candidates[chosen]['有效字节']),
                    物理上界字节=int(g['物理上界字节']),观察预留Zone=int(g['预留Zone']),
                    观察尾部字节=int(g['预留剩余字节']),实际复制字节=stats[1],实际重置区数=stats[2],
                    实际复制目标=sorted({int(p['目标Zone']) for p in copies
                        if int(p['源Zone'])==chosen and p['复制成功']=='1'})))
    return dict(补充预留资格候选数=admitted,无空目标时选中的ZC轮次=proof,
        无空目标时选中且重置成功次数=sum(p['实际重置区数']==1 for p in proof),
        已验证='按当前日志重算资格布尔关系、覆盖和物理上界，关联真实选区及ZC统计',
        边界='观察预留不是实际目标；Reset稳定性由已核对源码及运行日志支持，不声称原子锁定目标或独立重放全部并发变化')
