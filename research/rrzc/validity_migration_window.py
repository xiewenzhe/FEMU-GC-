"""只依据过去迁移标记审核有效量趋势；不读取未来维护或删除事件。"""
from analyze_validity_signal import trend, SECOND

def index_markers(markers):
    result = {}
    for row in markers:
        key = (row['批次'], row['Zone'], row['侧'])
        if row['侧'] not in ('前', '后') or key in result:
            raise ValueError('迁移标记重复或侧别无效')
        result[key] = row
    return result

def check_window(before, after, begin, end):
    """与C++窗口检查同边界；begin/end是主机单调时钟绝对纳秒。"""
    if before is None or after is None:
        return dict(可知=False,原因='缺少过去窗口迁移标记，不能把旧日志补成干净')
    try:
        keys=('启用','可信','实例','创建绝对纳秒','冻结绝对纳秒','轨迹原点绝对纳秒','序号','活动数','末次结束绝对纳秒')
        a={k:int(before[k]) for k in keys};b={k:int(after[k]) for k in keys}
        if any(v<0 for r in (a,b) for v in r.values()):raise ValueError('负计数')
    except (KeyError,TypeError,ValueError):
        return dict(可知=False,原因='迁移标记字段缺失或不是非负整数')
    if a['启用']!=1 or b['启用']!=1:
        return dict(可知=False,原因='迁移统计未启用')
    if a['可信']!=1 or b['可信']!=1 or not a['实例'] or a['实例']!=b['实例'] or a['创建绝对纳秒']!=b['创建绝对纳秒']:
        return dict(可知=False,原因='统计损坏或统计实例改变')
    if before['Zone']!=after['Zone'] or a['轨迹原点绝对纳秒']!=b['轨迹原点绝对纳秒']:
        return dict(可知=False,原因='Zone或轨迹时钟原点改变')
    if begin>end or a['创建绝对纳秒']>begin or a['冻结绝对纳秒']<a['创建绝对纳秒'] or a['冻结绝对纳秒']>begin or b['冻结绝对纳秒']<end or b['冻结绝对纳秒']<a['冻结绝对纳秒']:
        return dict(可知=False,原因='历史不足或快照未包围观察窗口')
    if b['序号']<a['序号'] or b['末次结束绝对纳秒']<a['末次结束绝对纳秒'] or a['末次结束绝对纳秒']>a['冻结绝对纳秒'] or b['末次结束绝对纳秒']>b['冻结绝对纳秒']:
        return dict(可知=False,原因='迁移序号或时间无效')
    touched=bool(a['活动数'] or b['活动数'] or a['序号']!=b['序号'] or (b['序号'] and b['末次结束绝对纳秒']>=begin))
    return dict(可知=True,无迁移触及=not touched,
        原因='观察窗口含迁移活动或完成边界' if touched else '历史窗口内无已观测迁移触及')

def clean_trend(history,current,seconds,parts,markers):
    """检查完整实际观察跨度，而非只检查恰好10／30秒的名义范围。"""
    signal=trend(history,current,seconds,parts)
    result=dict(可用=False,事前趋势=signal)
    if not signal['可知']:
        result['历史迁移']=dict(可知=False,原因='有效量历史自身未知，不推断自然退役')
        return result
    cutoff=int(current['查询开始纳秒'])
    anchor=[z for z in history if int(z['查询结束纳秒'])<cutoff and
            int(z['查询结束纳秒'])<=cutoff-seconds*SECOND][-1]
    before=markers.get((anchor['批次'],anchor['Zone'],'前'))
    after=markers.get((current['批次'],current['Zone'],'后'))
    try:origin=int(before['轨迹原点绝对纳秒']) if before is not None else 0
    except (KeyError,TypeError,ValueError):
        result['历史迁移']=dict(可知=False,原因='迁移标记时钟原点无效')
        return result
    # 原点字段损坏应返回未知，不使旧归档或坏记录被当成干净样本。
    audit=check_window(before,after,origin+int(anchor['查询开始纳秒']),origin+int(current['查询结束纳秒']))
    result['历史迁移']=audit
    result['可用']=audit['可知'] and audit.get('无迁移触及',False)
    result['边界']='仅排除已接线迁移复制／映射提交造成的污染；Reset仍由趋势身份检查，不能据此宣称寿命预测或避免RR收益。'
    return result
