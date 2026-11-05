#!/usr/bin/env python3
"""独立核验相同追加写入下，初始短窗优势是否保留；不更改原模型。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from analyze_all_blocks import analyze_arm,require
from gc_path_trace import fields

def amounts(text):
    zc=[int(v) for v in re.findall(r'ZC 统计：轮次 \d+，本轮迁移 (\d+) 字节',text)]
    rr=[int(v) for v in re.findall(r'RR 统计：尝试 \d+ 次，本次迁移 (\d+) 字节',text)]
    return dict(ZC次数=len(zc),RR次数=len(rr),ZC迁移字节=sum(zc),RR迁移字节=sum(rr),总迁移字节=sum(zc)+sum(rr))

def copies(text):
    lines=text.splitlines();result=[]
    for i,line in enumerate(lines):
        if 'V1后续迁移复制：' not in line:continue
        row=fields(line,'V1后续迁移复制：');require(row['复制成功']=='1','记录到失败复制')
        submitted=False
        for following in lines[i+1:]:
            if 'V1后续映射提交：' not in following:continue
            commit=fields(following,'V1后续映射提交：')
            if commit['文件编号']==row['文件编号']:
                require(commit['成功']=='1','复制之后文件映射提交失败');submitted=True;break
        require(submitted,'缺少复制后的成功映射提交')
        result.append(dict(文件编号=int(row['文件编号']),文件名=row['文件名'],源Zone=int(row['源Zone']),
            目标Zone=int(row['目标Zone']),有效字节=int(row['有效字节'])))
    return result

def arm(root,branch):
    result=analyze_arm(root);raw=(root/'zenfs.run.log').read_text().split('全块业务计量截止：',1)[0]
    marker='追加阶段边界：阶段=初始窗口结束'
    require(raw.count(marker)==1,'缺少唯一的追加阶段起点')
    before,after=raw.split(marker,1)
    first=amounts(before);later=amounts(after)
    require(first['总迁移字节']+later['总迁移字节']==result['总迁移统计字节'],'阶段迁移账目不闭合')
    with (root/'阶段空间.csv').open() as f:phases=list(csv.DictReader(f))
    expected=['初始窗口结束','存活条件执行后',*[f'追加写入{i}' for i in range(1,5)],'追加观察结束']
    require([r['阶段'] for r in phases]==expected,'阶段记录缺失或顺序错误')
    require([int(r['追加逻辑字节']) for r in phases]==[0,0,32<<20,64<<20,96<<20,128<<20,128<<20],
        '追加工作量不同或不完整')
    require(phases[0]['冷文件存在']=='1','初始窗口冷文件已经退役')
    require(all(r['冷文件存在']==('1' if branch=='keep' else '0') for r in phases[1:]),'实际存活条件与分支不一致')
    operations=[]
    for line in after.splitlines():
        if '追加业务写入：' in line:operations.append(fields(line,'追加业务写入：'))
    require([int(r['序号']) for r in operations]==list(range(4)),'追加操作记录缺失')
    require(all(int(r['逻辑字节'])==32<<20 for r in operations),'追加文件字节不同')
    end=result['结束空间'];last=phases[-1]
    for a,b in (('结束空闲','空闲字节'),('结束有效','有效字节'),('结束可回收','可回收字节')):
        require(int(end[a])==int(last[b]),'最后阶段与结束空间不一致')
    require(all(int(r['最高RC'])<=1024 for r in phases),'严格RR阈值超限')
    result.update(初始窗口=first,追加窗口=later,阶段空间=phases,追加固定操作=operations,
        初窗实际复制=copies(before),追加实际复制=copies(after))
    return result

def analyze(root):
    result={};provenance={}
    for branch in ('keep','retire'):
        arms={m:arm(root/(branch+'-'+m),branch) for m in ('greedy','blocks')}
        a,b=arms['greedy'],arms['blocks']
        require(a['固定输入']==b['固定输入'],'初始读取轨迹不同')
        require(a['追加固定操作']==b['追加固定操作'],'追加业务操作不同')
        for k in ('冷块RC','热块RC','后续请求','热偏移','空闲','非空闲'):
            require(a['初始状态'][k]==b['初始状态'][k],'初始条件不一致：'+k)
        require(int(a['结束空间']['结束有效'])==int(b['结束空间']['结束有效']),'相同业务后逻辑有效量不同')
        result[branch]=dict(两组=arms,全块减Greedy=dict(
            总迁移字节=b['总迁移统计字节']-a['总迁移统计字节'],
            ZC次数=b['ZC次数']-a['ZC次数'],RR次数=b['RR次数']-a['RR次数'],
            结束空闲字节=int(b['结束空间']['结束空闲'])-int(a['结束空间']['结束空闲']),
            结束可回收字节=int(b['结束空间']['结束可回收'])-int(a['结束空间']['结束可回收'])))
        for mode in arms:
            for n in ('zenfs.run.log','run.log','阶段空间.csv','结束空间.csv','输入与映射.csv','源区Reset核验.csv'):
                p=root/(branch+'-'+mode)/n;provenance[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result,provenance

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('guest_root',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result,provenance=analyze(a.guest_root);a.output.mkdir(exist_ok=True)
    for name,data in (('后续独立核验.json',result),('分析来源.json',provenance)):
        with (a.output/name).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    lines=['# 相同追加写入下的联合维护成本','',
        '4GiB受控文件I/O，RR1024、GC20%保持不变；原模型对象和静态库未修改。不是db_bench或YCSB性能/稳态结果。',
        '每臂重建同样NAND/RC及文件，先观察25秒，再保留或删除冷文件，双方均追加相同4×32MiB文件并等待12秒原生GC。',
        '未来退役安排只属于负载，不作为模型输入；不强制某一组补GC，也不按结果改变工作量。','',
        '| 存活分支 | 策略 | 初窗迁移MiB | 追加维护MiB | 总迁移MiB | ZC | RR | 维护Reset | 进程内全部Reset | 结束空闲MiB | 满区可回收MiB |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for branch,pair in result.items():
        for mode,r in pair['两组'].items():
            end=r['结束空间']
            lines.append(f"| {branch} | {mode} | {r['初始窗口']['总迁移字节']/(1<<20):.3f} | {r['追加窗口']['总迁移字节']/(1<<20):.3f} | {r['总迁移统计字节']/(1<<20):.3f} | {r['ZC次数']} | {r['RR次数']} | {r['维护统计Reset次数']} | {r['全部成功Reset含元数据']} | {int(end['结束空闲'])/(1<<20):.3f} | {int(end['结束可回收'])/(1<<20):.3f} |")
    lines+=['','迁移是ZenFS维护统计，追加业务写入128MiB不计入维护复制。所有存活候选和追加文件完整内容校验，校验读取在业务截止后单独划界。',
        '删除产生的无有效引用Zone直接Reset保留在原始日志，不冒充一次带有效复制的ZC。维护Reset和全部成功Reset含元数据分别记录在JSON。',
        '进程内全部Reset包含元数据和文件删除回收，不包含各臂业务启动前的共同全盘初始化Reset。满区可回收来自GetReclaimableSpace，不包括未写满Zone内部所有无效字节。',
        '结束空闲和待回收量不同时，只报告差异；不强行声称相同终态的收益，也不以单轮证明长期或普遍收益。','']
    with (a.output/'后续空间分析.md').open('x') as f:f.write('\n'.join(lines))
    print(json.dumps({k:v['全块减Greedy'] for k,v in result.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
