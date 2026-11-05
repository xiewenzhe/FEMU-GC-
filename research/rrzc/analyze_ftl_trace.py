#!/usr/bin/env python3
"""比较同一隔离客机两次完整Reset后的FTL输入及物理状态；不推定性能收益。"""
import argparse
import json
import mmap
from pathlib import Path
import struct
from itertools import zip_longest

ROW=struct.Struct('<8Q')
FIELDS=('操作码','起始LBA','扇区数或Reset区数','标记','PPA或映射摘要','RC或缓存摘要','状态','返回结果')


def analyze(path):
    with path.open('rb') as stream:
        with mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
            if data[:8]!=b'RRZCFT01' or (len(data)-8)%ROW.size:
                raise ValueError('FTL头或记录长度异常')
            count=(len(data)-8)//ROW.size
            def row(i):return ROW.unpack_from(data,8+i*ROW.size)
            # Select All实际只把非Empty区交给FTL；不能假定每次都会擦64区。
            # 此隔离负载只有g1/g2恢复做多区Reset；普通维护每次只Reset一个区。
            bulk=[(i,row(i)) for i in range(count) if row(i)[0]==256 and row(i)[2]>1]
            if len(bulk)!=2 or any(not 1<r[2]<=64 for _,r in bulk):
                raise ValueError('预期g1/g2两次恢复多区Reset，实际'+str(bulk))
            starts=[i+1 for i,_ in bulk]
            a,b=starts;end=count;lengths=[b-a-1,end-b]
            first=None
            relevant={1,2,125,194,256}
            def commands(start,length,allowed=relevant):
                for j in range(length):
                    r=row(start+j)
                    if r[0] in allowed:yield j,r
            for ordinal,pair in enumerate(zip_longest(commands(a,lengths[0]),commands(b,lengths[1]))):
                if None in pair:break
                (ia,x),(ib,y)=pair
                if x!=y:
                    context=[]
                    for offset in range(-3,4):
                        ja,jb=ia+offset,ib+offset
                        if 0<=ja<lengths[0] and 0<=jb<lengths[1]:
                            context.append(dict(g1相对命令序号=ja,g2相对命令序号=jb,
                                g1=dict(zip(FIELDS,row(a+ja))),g2=dict(zip(FIELDS,row(b+jb)))))
                    # 同一个输入命令的RC或PPA不同，只能判为采样状态分歧；不直接归因某个Bug。
                    reason='设备收到的输入命令或交错不同' if x[:4]!=y[:4] else '相同命令的映射、RC、缓存或反馈不同'
                    first=dict(相关命令序号=ordinal,相对命令序号=[ia,ib],分类=reason,
                        差异字段=[k for k,u,v in zip(FIELDS,x,y) if u!=v],上下文=context)
                    break
            def semantic(start,length):
                for j,r in commands(start,length):
                    op,lba,nlb,marker=r[:4]
                    if op==256:
                        if nlb!=1:raise ValueError('恢复区间内出现未解释多区Reset')
                        yield j,(op,lba,0)
                    else:
                        for lpn in range(lba//8,(lba+nlb-1)//8+1):
                            yield j,(op,lpn,marker if op==194 else 0)
            semantic_first=None
            for i,pair in enumerate(zip_longest(semantic(a,lengths[0]),semantic(b,lengths[1]))):
                if pair[0] is None or pair[1] is None or pair[0][1]!=pair[1][1]:
                    semantic_first=dict(页访问序号=i,g1=pair[0],g2=pair[1]);break
            read_first=None
            for i,pair in enumerate(zip_longest(commands(a,lengths[0],{2,194}),commands(b,lengths[1],{2,194}))):
                if pair[0] is None or pair[1] is None or pair[0][1]!=pair[1][1]:
                    read_first=dict(读命令序号=i,g1=pair[0],g2=pair[1]);break
            def coalesced(start,length):
                pending=None
                for j,r in commands(start,length):
                    value=(r[0],r[1],r[2],r[3] if r[0]==194 else 0)
                    if pending is not None:
                        old_index,old=pending
                        if old[0]==value[0]==1 and old[1]+old[2]==value[1]:
                            pending=(old_index,(1,old[1],old[2]+value[2],0));continue
                        yield pending
                    pending=(j,value)
                if pending is not None:yield pending
            coalesced_first=None
            for i,pair in enumerate(zip_longest(coalesced(a,lengths[0]),coalesced(b,lengths[1]))):
                if pair[0] is None or pair[1] is None or pair[0][1]!=pair[1][1]:
                    coalesced_first=dict(合并命令序号=i,g1=pair[0],g2=pair[1]);break
            return dict(文件=str(path),总命令数=count,恢复多区Reset=bulk,Reset后记录起点=starts,
                两臂记录数=lengths,首差=first,按4KiB页展开的输入首差=semantic_first,读命令及PPA_RC首差=read_first,
                合并相邻连续写后的输入首差=coalesced_first,
                边界='仅FTL实际接收顺序及所采集映射/RC/缓存摘要；忽略不访问NAND的Open/Close/Report等命令，其请求池slba/nlb不作有效样本。按页展开用于区分分包与实际页访问交错，不含Admin查询、数据字节及全部内存。比较包含准备、打开及业务；新诊断二进制不代表原并发性能。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();result=analyze(args.path)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
