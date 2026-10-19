"""独立重算成本排序；旧结果默认禁止混入新排序日志。"""
from fractions import Fraction
from analyze_v1_shadow_stream import fields,require,EXTRA,SECOND
from analyze_v1_growth_stream import qualifies

def choose_cost(candidates,estimates,original):
    if not original[2]:return original
    greedy=next(c for c in candidates if int(c['Zone'])==original[0])
    options=[c for c in candidates if qualifies(c) and int(c['Zone'])!=original[0] and
        estimates.get(int(c['Zone'])) is not None and estimates[int(c['Zone'])]<=10*SECOND and
        max(0,int(c['有效字节'])-int(greedy['有效字节']))<=EXTRA]
    require(options,'原准入集合为空')
    best=min(options,key=lambda c:(Fraction(int(c['有效字节']),int(c['容量字节'])-int(c['有效字节'])),
                                 estimates[int(c['Zone'])],int(c['Zone'])))
    z=int(best['Zone'])
    return original[0],z,True,max(0,int(best['有效字节'])-int(greedy['有效字节'])),estimates[z],'成本优先'

class CostRankLog:
    def __init__(self,enabled):
        require(enabled in (None,False,True),'排序模式未知')
        self.enabled=enabled;self.started=False;self.pending=None;self.count=0
    def observe(self,line):
        if '成本排序：' in line:
            require(self.enabled is not None and not self.started,'未预期的排序日志')
            require(line.endswith(f'成本排序：启用={int(self.enabled)}，默认ETA优先，仅在原紧迫准入集合内排序，尾部和源复核不变'),'入口异常')
            self.started=True
        if '成本排序核验：' in line:
            require(self.started and self.pending is None,'排序记录未闭合或未启用')
            self.pending=fields(line,'成本排序核验：')
    def resolve(self,rid,candidates,estimates,original):
        if self.enabled is None:return original
        require(self.started and self.pending is not None,'缺排序记录')
        effective=choose_cost(candidates,estimates,original) if self.enabled else original
        expected=dict(轮次=str(rid),启用=str(int(self.enabled)),ETA建议Zone=str(original[1] or 0),
            生效建议Zone=str(effective[1] or 0),排序改变=str(int(original[1]!=effective[1])),
            ETA预测纳秒=str(original[4]),生效预测纳秒=str(effective[4]),
            ETA额外字节=str(original[3]),生效额外字节=str(effective[3]))
        require(self.pending==expected,'成本排序日志与独立重算不同')
        self.pending=None;self.count+=1
        return effective
    def finish(self,count):
        require(self.pending is None and (self.enabled is None or self.started and self.count==count),'排序覆盖不完整')
