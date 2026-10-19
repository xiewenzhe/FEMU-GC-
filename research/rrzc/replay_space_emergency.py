"""独立重算空间紧急Greedy；保留全部原始复制与Reset账目。"""
from analyze_v1_shadow_stream import fields,require
from analyze_controlled_zc import ZC_STATS


def replay(lines):
    require(any('空间紧急回收配置：启用=1，' in row for row in lines),'未启用空间紧急回收')
    groups=[];current=[]
    for row in lines:
        if 'GC 检查：' in row and current:groups.append(current);current=[]
        current.append(row)
    if current:groups.append(current)
    filtered=[];decisions=[]
    for group in groups:
        ds=[fields(row,'空间紧急回收决策：') for row in group if '空间紧急回收决策：' in row]
        if not ds:filtered.extend(group);continue
        require(len(ds)==1,'同一GC轮多个空间紧急决策')
        d=ds[0]
        require(int(d['等待数'])>0 and d['正常安全候选存在']=='0' and d['回收预算字节']=='1'
                and d['RR感知改选']=='0' and d['专用预留限定']=='1','空间紧急准入不符')
        cs=[fields(row,'空间紧急候选：') for row in group if '空间紧急候选：' in row]
        require(len({c['Zone'] for c in cs})==len(cs),'空间候选重复')
        for c in cs:
            v,cap=int(c['有效字节']),int(c['容量字节'])
            require(0<v<cap,'空间紧急候选不具备正回收量')
            normal=100-100*v//cap>int(d['原无效比例门槛'])
            require(int(c['原门槛资格'])==int(normal),'原门槛资格重算不符')
        safe=[c for c in cs if c['覆盖资格']==c['预留资格']==c['源稳定']=='1']
        # 正常池还可以使用普通空目标，必须以正常候选日志另行重算。
        normals=[fields(row,'简单双因素候选：') for row in group if '简单双因素候选：' in row]
        require(not any(c['覆盖资格']==c['空目标资格']=='1' and
                        0<int(c['有效字节'])<int(c['容量字节']) for c in normals),'存在正常安全候选却放宽门槛')
        require(safe,'空间紧急决策没有安全候选')
        chosen=min(safe,key=lambda c:(int(c['有效字节']),int(c['Zone'])))
        require(d['实际Zone']==chosen['Zone'] and d['实际有效字节']==chosen['有效字节'],'空间紧急Greedy重算不符')
        actual=[fields(row,'简单双因素实际集合：') for row in group if '简单双因素实际集合：' in row]
        require(len(actual)==1 and actual[0]['实际选中']=='1' and actual[0]['Zone']==chosen['Zone'],'实际集合不是同一单区')
        rs=[fields(row,'空间紧急回收结果：') for row in group if '空间紧急回收结果：' in row]
        stats=[tuple(map(int,match.groups())) for row in group if (match:=ZC_STATS.search(row))]
        require(len(rs)==len(stats)==1,'空间紧急维护账目缺失')
        r=rs[0]
        require(r['实际Zone']==chosen['Zone'] and r['迁移成功']=='1' and
                r['本轮重置区数']=='1' and stats[0][2]==1 and
                int(r['本轮迁移字节'])==stats[0][1],'空间紧急迁移或Reset未完成')
        require(not any('独立约束决策：' in row or '留存收益建议：' in row or
                        '公共单区恢复决策：' in row for row in group),'空间紧急轮混入模型或重复应急')
        decisions.append(dict(d,本轮迁移字节=int(r['本轮迁移字节']),独立核验=True))
        filtered.extend(row for row in group if '简单双因素候选：' not in row and
                        '简单双因素实际集合：' not in row)
    return filtered,decisions
