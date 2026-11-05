"""只读RR健康验收：完整风险帧、同代际等待时间和内容类别，绝不代替RR执行。"""
import re

SECOND=10**9
GRACE=30*SECOND

def fields(line,tag):
    return dict(part.split('=',1) for part in line.split(tag,1)[1].strip().split('，'))

def inspect(text,strict=True,now_ns=None):
    frames=[];current=None;allocations=0;delays=0;errors=[];deferred={}
    for line in text.splitlines():
        if 'RR风险帧：' in line:
            if current:frames.append(current)
            r=fields(line,'RR风险帧：');current=(int(r['时点纳秒']),int(r['区数']),{})
        elif 'RR风险状态：' in line:
            r=fields(line,'RR风险状态：');t=int(r['时点纳秒']);z=int(r['Zone'])
            if not current or t!=current[0]:continue
            if z in current[2]:errors.append('同风险帧Zone重复')
            current[2][z]=r
        elif '公共内容分配：' in line:
            r=fields(line,'公共内容分配：');allocations+=1
            before,want,actual=(int(r[k]) for k in ('原类别','请求类别','实际类别'))
            if want not in (1,2) or actual!=want or before not in (0,want):errors.append('发现跨类别分配')
        elif 'RR 延后：' in line:
            delays+=1;m=re.search(r'^(\S+)\s+\S+\s+.*RR 延后：Zone (\d+)',line)
            if m:
                # 旧日志的同区连续延期用于诊断；不能当作独立RR次数。
                z=m[2];deferred[z]=deferred.get(z,0)+1
    if current:frames.append(current)
    complete=[f for f in frames if len(f[2])==f[1] and f[1]>0]
    if strict and (not complete or len(complete)!=len(frames)):
        errors.append('缺少完整RR风险帧')
    if strict and '公共内容隔离配置：启用=1，' not in text:errors.append('内容隔离未确认开启')
    pending={};max_wait=0;max_rc=0;overdue=[];unknown=0;last_time=None;last_zones=None
    for t,count,zones in complete:
        if last_time is not None and (t<=last_time or t-last_time>GRACE):errors.append('RR检查间隔异常或超过30秒')
        if last_zones is not None and set(zones)!=last_zones:errors.append('风险帧Zone集合变化')
        last_time=t;last_zones=set(zones)
        for z,r in zones.items():
            if r['可信']!='1':
                unknown+=1
                # 不可信不能当作风险已消失，也不拼接新身份。
                if z in pending and t-pending[z][1]>GRACE:errors.append('持续风险跨未知观察超过30秒')
                continue
            rc,threshold,valid=(int(r[k]) for k in ('最大RC','阈值','有效'))
            max_rc=max(max_rc,rc)
            key=(r['代际'],r['序号'])
            if not valid or not threshold or rc<threshold:
                pending.pop(z,None);continue
            if z not in pending or pending[z][0]!=key:pending[z]=(key,t)
            wait=t-pending[z][1];max_wait=max(max_wait,wait)
            if wait>GRACE:
                overdue.append(dict(Zone=z,代际=r['代际'],等待秒=wait/SECOND,最大RC=rc,含非SST=r['含非SST']))
    if overdue:errors.append('有效Zone超阈值超过30秒仍未维护')
    if now_ns is not None and last_time is not None and now_ns-last_time>GRACE:
        errors.append('最新完整RR检查已超过30秒未更新')
    return dict(验收通过=not errors,错误=sorted(set(errors)),完整风险帧数=len(complete),
        分类分配次数=allocations,RR延期观察数=delays,分区延期观察数=deferred,
        最长已观察超阈值等待秒=max_wait/SECOND,最大已观察RC=max_rc,未知观察数=unknown,
        超期明细=overdue,末帧仍超阈值区数=len(pending),
        口径='30秒仅为验收上限，不修改RR阈值或触发；末帧短期待维护单独报告，不冒充零风险；未知观察不作已维护。')
