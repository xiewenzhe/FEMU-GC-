"""独立核对原数值门槛和真实非零判空，关联舍入100%区的实际回收。"""
import re
from analyze_v1_shadow_stream import fields,require
from analyze_controlled_zc import ZC_STATS

def inspect(lines):
    require(any('真实有效字节判空配置：启用=1，' in s for s in lines),'真实有效字节判空未确认')
    threshold=None;tiny=set();chosen=None;proof=[];count=0
    for line in lines:
        if 'GC 检查：' in line:
            r=fields(line,'GC 检查：')
            free=int(r['空闲比例'].removesuffix('%'))
            require(r['启动水平']=='20%','GC启动水平改变')
            threshold=100-3*(20-free) if free<=20 else None
            tiny=set();chosen=None
        if 'ZC 候选：' in line:
            m=re.search(r'Zone (\d+)，无效比例 (\d+)%.*有效数据 (\d+) 字节.*选中 ([01])',line)
            require(m and threshold is not None,'候选没有对应原GC检查')
            z,g,v,selected=map(int,m.groups());expected=v>0 and g>threshold
            require(g==100-100*v//(256<<20) and selected==int(expected),'原门槛或真实判空重算不符')
            if v and g==100:
                count+=1
                if expected:tiny.add(z)
        if '简单双因素实际集合：' in line:
            r=fields(line,'简单双因素实际集合：')
            if r.get('实际选中')=='1':chosen=int(r['Zone'])
        if (m:=ZC_STATS.search(line)) and chosen in tiny:
            round_,moved,reset=map(int,m.groups())
            proof.append(dict(ZC轮次=round_,源Zone=chosen,逻辑复制字节=moved,源区重置数=reset))
    return dict(舍入100但非零有效量候选次数=count,实际清理记录=proof,
        已核对='逐区原20%与斜率3的数值门槛、显示比例、真实非零判断及相应ZC统计')
