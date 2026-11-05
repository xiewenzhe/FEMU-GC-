"""直接审计单区Greedy的实际集合标记，不将其他模型日志格式误当空事务。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import accounting


def audit(root,allow_retired_rollback=False):
    root=Path(root);lines=(root/'zenfs.log').read_text().splitlines()
    active={};done=[];window=False;epochs={};retired=set()
    for number,line in enumerate(lines,1):
        if '受控放置业务开始：' in line:window=True
        if '受控放置业务截止：' in line:
            assert not active,'业务截止时维护未闭合'
            window=False
        tid=line.split()[1] if len(line.split())>1 else None
        rr=re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节',line)
        zc=fields(line,'简单双因素实际集合：') if '简单双因素实际集合：' in line else None
        if rr or (zc and zc['实际选中']=='1'):
            assert window,'维护发生在业务计量窗之外'
            assert tid not in active,'同线程维护重入'
            kind='RR' if rr else 'ZC';zone=int(rr[1] if rr else zc['Zone'])
            if zc:assert zc['模式']=='valid','实际执行了非Greedy策略'
            active[tid]=dict(类型=kind,Zone=zone,开始行=number,原始选择行=line,
                源代际=epochs.get(zone,0),复制=[],提交=[],Reset=[])
            if allow_retired_rollback:active[tid]['退役撤销']=[]
        if 'V1后续文件退役：' in line:
            retired.add(int(fields(line,'V1后续文件退役：')['文件编号']))
        if 'V1后续迁移复制：' in line:
            row=fields(line,'V1后续迁移复制：')
            assert row['复制成功']=='1' and tid in active,'复制失败或缺少实际维护选择'
            task=active[tid]
            assert int(row['源Zone'])==task['Zone']
            assert allow_retired_rollback or int(row['文件编号']) not in retired
            item=dict(行=number,字段=row)
            if allow_retired_rollback:item['复制前已退役']=int(row['文件编号']) in retired
            task['复制'].append(item)
        if allow_retired_rollback and '公共迁移退役处理：' in line:
            row=fields(line,'公共迁移退役处理：')
            assert tid in active and int(row['文件编号']) in retired,'撤销缺少事务或实际退役证据'
            assert int(row['源Zone'])==active[tid]['Zone']
            assert all(row.get(k)=='1' for k in ('已退役','未提交映射已撤销','目标释放成功')),'退役副本撤销或释放失败'
            active[tid]['退役撤销'].append(dict(行=number,字段=row))
        if 'V1后续映射提交：' in line:
            row=fields(line,'V1后续映射提交：')
            assert tid in active and row['成功']=='1','映射提交失败或不属于维护'
            active[tid]['提交'].append(dict(行=number,字段=row))
        if 'Zone 重置诊断：' in line:
            row=fields(line,'Zone 重置诊断：');zone=int(row['Zone'])
            assert row['状态']=='成功'
            if tid in active:active[tid]['Reset'].append(dict(行=number,字段=row))
            epochs[zone]=epochs.get(zone,0)+1
        if 'ZC 统计：' in line or 'RR 统计：' in line:
            assert tid in active,'维护汇总缺少真实选区标记'
            task=active.pop(tid);kind='RR' if 'RR 统计：' in line else 'ZC'
            assert task['类型']==kind and task['复制']
            size=int(re.search(r'(?:本轮|本次)迁移 (\d+) 字节',line)[1])
            assert size==sum(int(x['字段']['有效字节']) for x in task['复制'])
            for item in task['复制']:
                fid=item['字段']['文件编号']
                committed=any(item['行']<commit['行']<reset['行']<number and
                    commit['字段']['文件编号']==fid and int(reset['字段']['Zone'])==task['Zone']
                    for commit in task['提交'] for reset in task['Reset'])
                rolled_back=allow_retired_rollback and any(item['行']<cancel['行']<reset['行']<number and
                    cancel['字段']['文件编号']==fid and int(reset['字段']['Zone'])==task['Zone']
                    for cancel in task['退役撤销'] for reset in task['Reset'])
                assert committed != bool(rolled_back),'复制必须且只能通过映射提交或退役撤销后再Reset'
                if item.get('复制前已退役'):
                    assert rolled_back and not committed,'已退役复制必须撤销，不能以成功映射掩盖'
            task.update(结束行=number,迁移字节=size);done.append(task)
    assert not active and not window
    ledger=accounting(root,'zenfs.log')
    assert len(done)==ledger['ZC次数']+ledger['RR次数'], '真实维护与汇总次数不同'
    assert sum(t['迁移字节'] for t in done)==ledger['总迁移字节']
    assert ledger['复制日志与维护汇总相同'] and not ledger['映射提交失败数']
    copied={}
    for task in done:
        for row in task['复制']:
            name=row['字段']['文件名'];values=copied.setdefault(name,dict(ZC字节=0,RR字节=0,次数=0))
            values[task['类型']+'字节']+=int(row['字段']['有效字节']);values['次数']+=1
    result=dict(通过=True,维护账目=ledger,事务=done,按文件复制=copied,
        源日志SHA256=hashlib.sha256((root/'zenfs.log').read_bytes()).hexdigest(),
        边界='直接读取简单双因素实际集合与RR开始标记；不改写原日志、不代填决策；代际为本日志内Reset序数')
    if allow_retired_rollback:
        result['退役撤销复制字节']=sum(int(row['字段']['有效字节']) for tx in done for row in tx['复制']
            if any(c['字段']['文件编号']==row['字段']['文件编号'] for c in tx['退役撤销']))
        result['边界']+='；并发退役仅在实际撤销/目标释放成功且先于源Reset时接受，已复制字节仍全额计入'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path)
    args=parser.parse_args();root=args.results
    status=json.loads((root/'状态.json').read_text());archive=json.loads((root/'阶段归档.json').read_text())
    assert status['当前客机PID'] is None and archive['完整'] and archive['业务成功']
    source=root/status['标签'];reports={}
    for case in ('w-first','rr-first','cooled'):
        for k in (0,1):
            for r in (1,2):
                tag=f'{case}-k{k}-r{r}';reports[tag]=audit(source/tag)
    output=root/'单区Greedy完整事务补充核验.json'
    assert not output.exists(),'不覆盖已有审计'
    output.write_text(json.dumps(dict(通过=True,各臂=reports,
        审计源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        说明='原通用事务工具只识别统一决策，新增独立解析以覆盖单区Greedy真实选择标记；原始报告保留'),
        ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
