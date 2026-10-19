"""独立核对公共应急Greedy；从旧模型重放中分离应急轮，不隐藏原始日志。"""
from analyze_v1_shadow_stream import fields,require,BUDGET
from analyze_controlled_zc import ZC_STATS

def replay(lines):
    require(any('公共单区恢复配置：启用=1，' in s for s in lines),'未确认公共恢复入口')
    groups=[];current=[]
    for line in lines:
        if 'GC 检查：' in line and current:groups.append(current);current=[]
        current.append(line)
    if current:groups.append(current)
    filtered=[];decisions=[]
    for group in groups:
        emergency=[fields(s,'公共单区恢复决策：') for s in group if '公共单区恢复决策：' in s]
        if not emergency:filtered.extend(group);continue
        require(len(emergency)==1,'应急决策重复')
        d=emergency[0]
        require(d['RR感知改选']=='0' and int(d['原回收预算字节'])==BUDGET and d['应急回收预算字节']=='1',
            '应急参数或模型隔离不符')
        cs=[fields(s,'简单双因素候选：') for s in group if '简单双因素候选：' in s]
        require(not any(c['覆盖资格']==c['空目标资格']=='1' and
            0<int(c['有效字节'])<=int(c['容量字节']) and
            int(c['容量字节'])-int(c['有效字节'])>=BUDGET for c in cs),
            '真实可迁移的正常预算候选存在却进入应急')
        eligible=[c for c in cs if c['覆盖资格']==c['空目标资格']=='1' and
            0<int(c['有效字节'])<int(c['容量字节'])]
        chosen=min(eligible,key=lambda c:(int(c['有效字节']),int(c['Zone']))) if eligible else None
        require(d['存在']==str(int(chosen is not None)) and int(d['实际Zone'])==(int(chosen['Zone']) if chosen else 0)
            and int(d['实际有效字节'])==(int(chosen['有效字节']) if chosen else 0),'应急Greedy重算不符')
        actual=[fields(s,'简单双因素实际集合：') for s in group if '简单双因素实际集合：' in s]
        require(len(actual)==1 and int(actual[0]['实际选中'])==int(chosen is not None),'应急实际集合不是零或单区')
        stats=[tuple(map(int,m.groups())) for s in group if (m:=ZC_STATS.search(s))]
        results=[fields(s,'公共单区恢复维护结果：') for s in group if '公共单区恢复维护结果：' in s]
        require(not any('独立约束决策：' in s or '留存收益建议：' in s for s in group),'应急轮混入RR感知执行')
        if chosen:
            require(actual[0]['Zone']==d['实际Zone'] and len(stats)==len(results)==1,'应急维护账目缺失')
            r=results[0]
            require(r['实际Zone']==d['实际Zone'] and r['迁移成功']=='1' and r['本轮重置区数']=='1'
                and stats[0][1]==int(r['本轮迁移字节']) and stats[0][2]==1,'应急迁移或Reset未完成')
        else:require(not stats and not results,'无应急选择却执行了迁移')
        decisions.append(dict(d,独立核验=True))
        # 只从旧选择器重算输入中去掉已独立验过的候选及集合；原始账目仍全量核对。
        filtered.extend(s for s in group if '简单双因素候选：' not in s and '简单双因素实际集合：' not in s)
    return filtered,decisions
