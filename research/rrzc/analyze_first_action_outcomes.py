"""逐次核验真实改选、同文件后继复制与末端；不将跨臂后续状态冒称反事实。"""
from collections import defaultdict
import csv
import json
from pathlib import Path
import shutil

from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import stamp
from ycsb_af import digest,require

ZONE=128<<20


def arm(root,tag):
    report=json.loads((root/(tag+'-核验.json')).read_text());path=root/'逐臂采证'/tag
    lines=(path/'run.zenfs.log').read_text().splitlines();positions={};copies=[];by_file=defaultdict(list)
    for i,line in enumerate(lines,1):
        if '共同观测实际决策：' in line:
            r=fields(line,'共同观测实际决策：');positions[int(r['轮次'])]=(i,stamp(line))
    for task in report['事务']:
        for item in task['复制']:
            f=item['字段'];number=item['行']-1
            require(fields(lines[number-1],'V1后续迁移复制：')==f,'实际复制行与冻结事务断链')
            row=dict(行=number,类型=task['类型'],文件=int(f['文件编号']),字节=int(f['有效字节']),
                源Zone=int(f['源Zone']),源片段=int(f['源片段']),目标Zone=int(f['目标Zone']),
                目标片段=int(f['目标片段']),纳秒=stamp(lines[number-1]))
            copies.append(row);by_file[row['文件']].append(row)
    switched=[]
    for d in report['逐轮选择']:
        if not d['改选']:continue
        n=d['字段']['轮次'];selected=d['字段']['实际Zone'];line,ns=positions[n]
        task=next(t for t in report['事务'] if t['开始行']-1>line)
        require(task['类型']=='ZC' and task['Zone']==selected,'改选后第一次维护不是选中ZC')
        selected_row=next(c for c in d['候选'] if c['Zone']==selected)
        chosen=[]
        for item in task['复制']:
            number=item['行']-1;now=next(x for x in copies if x['行']==number)
            later=next((x for x in by_file[now['文件']] if x['行']>number),None)
            # 文件ID、物理源/目标和字节都吻合才称下一跳；映射拆分或改变保留未知。
            linked=later is not None and (later['源Zone'],later['源片段'],later['字节'])==(
                now['目标Zone'],now['目标片段'],now['字节'])
            hot=next(f for f in d['文件'] if f['Zone']==selected and f['文件']==now['文件'])
            chosen.append(dict(本次复制=now,当前热读=hot['热读'],当前抵扣=hot['抵扣'],
                下一同文件复制=later,下一跳同片段=linked,
                到下一复制秒=(later['纳秒']-now['纳秒'])/1e9 if later else None))
        switched.append(dict(轮次=n,决策行=line,GreedyZone=d['字段']['GreedyZone']//ZONE,
            实际Zone=selected//ZONE,候选=d['候选'],复制=chosen,本次真实复制字节=task['迁移字节'],
            评分抵扣=selected_row['抵扣'],ETA纳秒=selected_row['ETA']))
    totals={fid:dict(次数=len(rows),总字节=sum(x['字节'] for x in rows),
        ZC次数=sum(x['类型']=='ZC' for x in rows),RR次数=sum(x['类型']=='RR' for x in rows),复制=rows)
        for fid,rows in by_file.items()}
    return dict(改选=switched,按文件=totals,原日志SHA256=digest(path/'run.zenfs.log'))


def finish(root,output):
    require(not output.exists(),'不覆盖逐次改选报告')
    state=json.loads((root/'状态.json').read_text());stage=json.loads((root/'阶段归档.json').read_text())
    require(state.get('完整实验完成') and state['当前客机PID'] is None and stage['完整']
        and stage['业务成功'] and not stage['客机仍存活'],'来源未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'来源归档哈希不同')
    reports={t:arm(root,t) for t in ('p1','p2','m1','m2')}
    hot_ids={x['本次复制']['文件'] for t in ('m1','m2') for d in reports[t]['改选']
        for x in d['复制'] if x['当前热读']}
    output.mkdir(parents=True)
    result=dict(来源=str(root),各臂=reports,改选热文件=sorted(hot_ids),
        归档SHA256=state['镜像处理']['archive_sha256'],分析器SHA256=digest(Path(__file__)),
        边界='复制日志与映射账目中的实际逻辑文件及物理下一跳；只是所观察工作量，不推算改选反事实')
    (output/'逐次改选核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(Path(__file__),output/'分析源码.py')
    lines=['# 首区模型：真实改选与后继维护','',result['边界'],'',
        '|臂|轮次|Greedy区|实际区|本次复制MiB|评分抵扣MiB|ETA秒|热文件下一次复制|',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for tag,r in reports.items():
        for d in r['改选']:
            following=[]
            for c in d['复制']:
                if not c['当前热读']:continue
                nxt=c['下一同文件复制'];text='业务截止未见' if not nxt else (
                    f'{nxt["类型"]} {nxt["源Zone"]//ZONE}→{nxt["目标Zone"]//ZONE}，{c["到下一复制秒"]:.3f}秒，'
                    f'同片段={c["下一跳同片段"]}')
                following.append(text)
            lines.append(f'|{tag}|{d["轮次"]}|{d["GreedyZone"]}|{d["实际Zone"]}|'
                f'{d["本次真实复制字节"]/2**20:.3f}|{d["评分抵扣"]/2**20:.3f}|'
                f'{d["ETA纳秒"]/1e9:.3f}|'+ ';'.join(following)+'|')
    lines+=['','## 被改选的热文件在完整窗口中的复制量','',
        '|文件编号|臂|总复制次数|ZC／RR|总复制MiB|','|---:|---|---:|---|---:|']
    for fid in sorted(hot_ids):
        for tag,r in reports.items():
            x=r['按文件'].get(fid,dict(次数=0,ZC次数=0,RR次数=0,总字节=0))
            lines.append(f'|{fid}|{tag}|{x["次数"]}|{x["ZC次数"]}／{x["RR次数"]}|{x["总字节"]/2**20:.3f}|')
    lines+=['','若同一热文件在新目标上又很快被ZC搬走，上一笔评分抵扣不能自动解释为已经免掉独立维护。'
        '先按真实文件复制链计数，再与总复制和末端空间对照。源区Reset只清源区物理计数；'
        '下一跳用的是新目标的实际读状态，逻辑文件持续热读不代表以后不再需要RR。',
        '热文件连续存活只能说明届时可能仍有有效数据需要搬，不能证明这次搬迁会减少完整生命周期维护次数。'
        '原模型的文件失效线性外推有问题，新版只用存活热文件抵扣也可能仍高估可避免成本；两者不能混为一个已解决的问题。']
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();finish(a.root,a.output)
