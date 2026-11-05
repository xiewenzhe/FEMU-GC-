#!/usr/bin/env python3
"""只读定位复制源/目标及普通分配首差，区分已知物理分化和未采集的忙闲原因。"""
import argparse
import hashlib
import json
from pathlib import Path
from analyze_reproducibility import fields,difference,decisions


def analyze(source,output):
    copied={};allocated={};hashes={};logs={}
    for tag in ('g1','g2'):
        p=source/tag/'run.zenfs.log';logs[tag]=p.read_text().splitlines()
        hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        copied[tag]=[(i,fields(line,'V1后续迁移复制：')) for i,line in enumerate(logs[tag],1)
            if 'V1后续迁移复制：' in line]
        allocated[tag]=[(i,fields(line,'公共内容分配：')) for i,line in enumerate(logs[tag],1)
            if '公共内容分配：' in line and fields(line,'公共内容分配：')['阶段']=='普通']
    def compare(rows,keys):
        out=difference(*[[tuple(r[k] for k in keys) for _,r in rows[t]] for t in ('g1','g2')])
        index=out['首差序号从零起']
        if index is not None:
            out['首差上下文']={t:[dict(同类序号从零起=n,日志行=i,字段=r,
                前后原文=logs[t][max(0,i-5):i+3]) for n,(i,r) in
                enumerate(rows[t][max(0,index-1):index+2],max(0,index-1))] for t in ('g1','g2')}
        return out
    ranks=[decisions(source/t) for t in ('g1','g2')]
    selections=[r['选中'] for r in ranks[0]],[r['选中'] for r in ranks[1]]
    pick=difference(*selections)
    rows=[dict(轮次=i+1,g1Zone=a['选中']//(128<<20) if a['选中'] is not None else None,
        g2Zone=b['选中']//(128<<20) if b['选中'] is not None else None,
        成本资格相同=sorted(a['成本'])==sorted(b['成本'])) for i,(a,b) in enumerate(zip(*ranks))]
    result=dict(迁移目标首差=compare(copied,('目标Zone','目标片段')),
        迁移源首差=compare(copied,('文件编号','源Zone','源片段','有效字节')),
        普通分配首差=compare(allocated,('Zone','原类别','请求类别','实际类别')),
        Victim首差=pick,逐轮Victim=rows,
        边界='日志分别按同类出现顺序对齐，不代表原子同步状态；是否发生差异以对应首差字段为准。仅有这些日志时，不能确定具体哪个目标为何被跳过；Acquire及忙闲证据须另查目标扫描日志。',
        来源SHA256=hashes)
    with output.open('x') as stream:
        stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k in ('Victim首差','逐轮Victim')},ensure_ascii=False))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();analyze(args.source,args.output)
