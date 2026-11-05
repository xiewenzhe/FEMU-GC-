"""逐请求定位设备读命令摘要首差；摘要相同不等于完整物理IO已逐条证明相同。"""
from collections import deque
from itertools import zip_longest
from pathlib import Path
import re
import struct
from analyze_reproducibility import request_comparison,fields


def rows(path):
    with path.open('rb') as stream:
        if stream.read(8)!=b'RRZCIO01':raise ValueError('设备读摘要格式错误')
        index=-1;previous=None
        while raw:=stream.read(24):
            if len(raw)!=24:raise ValueError('设备读摘要尾部不完整')
            seq,count,fingerprint=struct.unpack('<3Q',raw)
            if seq!=(2**64-1 if index<0 else index):raise ValueError('设备读摘要请求编号不连续')
            if previous and (count<previous[1] or (count==previous[1] and fingerprint!=previous[2])):
                raise ValueError('没有新读命令时摘要变化，或累计命令倒退')
            yield (seq,count,fingerprint)
            previous=(seq,count,fingerprint);index+=1
        if index<0:raise ValueError('设备读摘要缺少起点记录')


def input_at(root,sequence):
    if sequence==2**64-1:return dict(位置='首次前台请求之前，可能包含打开时读取')
    with (root/'请求提交.bin').open('rb') as stream:
        assert stream.read(8)==b'RRZCREQ1'
        stream.seek(8+sequence*56);raw=stream.read(56)
        if len(raw)!=56:raise ValueError('设备读摘要对应请求不存在')
        seq,writer,index,key,_,_,_=struct.unpack('<7Q',raw)
    assert seq==sequence
    return dict(请求序号=seq,操作='WRITE' if writer else 'READ',角色内序号=index,Key=key,
        位置='该请求完成并排空已触发后台任务后；包含此前后台及迁移读命令')


def feedback(root):
    return [(n,fields(line,'严格RR命令反馈：')) for n,line in
        enumerate((root/'run.log').read_text().splitlines(),1) if '严格RR命令反馈：' in line]


def compare(a,b):
    first=None;first_amount=None;counts=[0,0];last=[None,None];before=deque(maxlen=2);after=[]
    for index,pair in enumerate(zip_longest(rows(a/'请求设备摘要.bin'),rows(b/'请求设备摘要.bin'))):
        if first_amount is None and index>0 and all(p is not None for p in pair) and all(p is not None for p in last):
            increment=[pair[n][1]-last[n][1] for n in (0,1)]
            if increment[0]!=increment[1]:
                first_amount=dict(请求=input_at(a,pair[0][0]),本请求期间设备读命令数=increment,
                    之前累计=last.copy(),之后累计=list(pair))
        for n,row in enumerate(pair):
            if row is not None:counts[n]+=1;last[n]=row
        if first is None and pair[0]!=pair[1]:
            sequence=pair[0][0] if pair[0] is not None else pair[1][0]
            first=dict(记录序号从零起=index,g1=pair[0],g2=pair[1],请求=input_at(a,sequence),此前两条=list(before))
        elif first is None:before.append(pair)
        elif len(after)<2:after.append(pair)
    if first is not None:first['后续两条']=after
    feedbacks=[feedback(root) for root in (a,b)]
    keys=('前台上下文','请求序号','写角色','角色序号','Key','LBA','搬迁','状态','结果')
    signatures=[[tuple(r[k] for k in keys) for _,r in values] for values in feedbacks]
    diff=next((i for i,pair in enumerate(zip_longest(*signatures)) if pair[0]!=pair[1]),None)
    feedback_diff=None
    if diff is not None:
        feedback_diff=dict(序号从零起=diff,上下文={tag:values[max(0,diff-1):diff+2]
            for tag,values in zip(('g1','g2'),feedbacks)})
    return dict(逐请求输入=request_comparison(a,b),设备摘要相同=first is None,
        请求记录数=[max(0,n-1) for n in counts],设备命令末累计=last,首差=first,
        首个每请求命令量差异=first_amount,
        稀疏反馈条数=list(map(len,feedbacks)),稀疏反馈首差=feedback_diff,
        边界='只记录严格路径尝试的4KiB读命令LBA、迁移标记、返回状态及结果，不含写入或Admin查询；64位累计指纹用于定位，不能代替完整命令/物理映射轨迹。')
