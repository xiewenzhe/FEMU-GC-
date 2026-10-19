"""核对完整空预留周转及显式牺牲尾部，不替代全键数据校验。"""
from analyze_v1_shadow_stream import fields,require

def inspect(lines):
    rows=[];sacrificed=0
    for line in lines:
        if '公共完整空预留周转：' not in line:continue
        r=fields(line,'公共完整空预留周转：')
        require(int(r['新剩余字节'])==256<<20 and r['新区独占保留']=='1','周转后不是完整独占空预留')
        old=r['旧存在']=='1';token=int(r['普通活跃令牌新增']);loss=int(r['牺牲尾部字节'])
        require(token in (0,1) and r['保留旧尾部'] in ('0','1'),'周转令牌或尾部标记非法')
        if old:
            require(r['新Zone']!=r['旧Zone'],'新旧预留相同')
            if loss:require(not token and r['保留旧尾部']=='0' and loss==int(r['旧剩余字节']),'牺牲尾部账目不符')
            else:require(r['保留旧尾部']=='1','未牺牲尾部却未记录保留')
        else:require(not token and not loss and r['保留旧尾部']=='0','无旧区却发生令牌或尾部费用')
        sacrificed+=loss;rows.append(r)
    return dict(完整空预留周转次数=len(rows),保留尾部次数=sum(r['保留旧尾部']=='1' for r in rows),
        牺牲尾部字节=sacrificed,牺牲尾部MiB=sacrificed/(1<<20),记录=rows,
        边界='周转不是额外数据复制；尾部牺牲单独报告，不从总搬运字节中扣除，也不计作模型收益')
