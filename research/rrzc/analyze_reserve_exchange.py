"""核对预留交换日志边界；最终数据与完整维护账目仍由原YCSB审计负责。"""
from analyze_v1_shadow_stream import fields,require

def inspect(lines):
    out=[];capacity=256<<20
    for line in lines:
        if '公共预留归属交换：' not in line:continue
        r=fields(line,'公共预留归属交换：')
        need,old_remaining,new_remaining,old_valid,new_valid=(int(r[k]) for k in
            ('需要字节','旧剩余字节','新剩余字节','旧有效字节','新有效字节'))
        require(r['旧Zone']!=r['新Zone'] and 0<old_remaining<need<=new_remaining<capacity,
            '预留交换的身份或容量关系不符')
        require(old_valid+old_remaining<=capacity and 0<new_valid and new_valid+new_remaining<=capacity,
            '预留交换有效量不符')
        require(all(r[k]=='1' for k in ('不复制','不Finish','不Reset','普通活跃令牌转移')),'交换行为标记不符')
        out.append(r)
    return dict(交换次数=len(out),交换记录=out,
        已核对='两个不同部分区、旧尾部不足、新尾部可容纳当前最小候选的物理上界；行为标记与已编译源码匹配',
        边界='全局活跃计数可能受并发线程改变；交换方法自身不增减令牌，未将两个不同时点的全局计数差当作独立证明')
