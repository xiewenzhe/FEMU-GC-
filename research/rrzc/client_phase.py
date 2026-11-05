"""读取显式客户端阶段计时；旧日志没有采样点时不反推精确关闭时间。"""
import re
from analyze_joint_opportunity import fields,require


def phases(text,ops):
    rows=[fields(s,'客户端阶段计时：') for s in text.splitlines() if '客户端阶段计时：' in s]
    if not rows:return dict(可知=False,原因='旧客户端未记录精确阶段时间，不能从API均值或十秒状态反推')
    require(len(rows)==1,'阶段计时重复');r=rows[0]
    require(all(re.fullmatch(r'\d+',v) for v in r.values()),'阶段时间字段非法')
    n={k:int(v) for k,v in r.items()}
    require(n['版本']==1 and n['线程数']==1 and n['完整']==1 and n['操作数']==ops,
        '阶段计时版本、线程数、完整性或操作数不符')
    a,b,c,d=[n[k] for k in ('前台开始','前台结束','Cleanup开始','Cleanup结束')]
    require(0<a<=b<=c<=d and n['前台纳秒']==b-a and n['Cleanup纳秒']==d-c and
        n['间隙纳秒']==c-b and n['工作线程总纳秒']==d-a,'阶段时间不闭合')
    return dict(可知=True,前台请求秒=n['前台纳秒']/1e9,Cleanup秒=n['Cleanup纳秒']/1e9,
        阶段间隙秒=n['间隙纳秒']/1e9,工作线程总秒=n['工作线程总纳秒']/1e9,
        口径='单线程循环前至最后请求返回，含生成/审计与API；Cleanup单独计时。不是逐API延迟或裸设备吞吐。')
