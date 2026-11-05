"""从真实Flush创建、来源标记、重挂载和布局联合核验；不把来源当寿命真值。"""
import argparse
import csv
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from analyze_sst_compaction import events
from analyze_sst_lifetime import arm as opportunities
from analyze_sst_interleaved import timing
from analyze_sampling_scope import suggestion_difference
from ycsb_af import digest, require


def parsed(text, marker):
    return [dict(行=n, **fields(line,marker)) for n,line in enumerate(text.splitlines(),1) if marker in line]


def verify_origins(root, initial):
    records=events(root/'run.rocksdb.log')
    flush_jobs={e['记录']['job'] for e in records if e['记录'].get('event')=='flush_started'}
    compact_jobs={e['记录']['job'] for e in records if e['记录'].get('event')=='compaction_started'}
    made={e['记录']['file_number']:e for e in records if e['记录'].get('event')=='table_file_creation'
          and e['记录'].get('status')=='OK'}
    tagged=parsed((root/'run.log').read_text(),'Flush来源W标记：')
    names={}
    for row in tagged:
        name=row['文件名'];match=re.fullmatch('/rrzc-dbbench/([0-9]+)\.sst',name)
        require(match and name not in names and name not in initial,'来源标签文件重复或覆盖显式H')
        number=int(match[1]);job=int(row['任务'])
        require(number in made and made[number]['记录']['job']==job and job in flush_jobs
                and job not in compact_jobs and row['标签']=='2','W标记不对应真实Flush创建')
        names[name]=row
    expected={n for n,r in made.items() if r['记录']['job'] in flush_jobs}
    require({int(n.rsplit('/',1)[1][:-4]) for n in names}==expected and names,'真实Flush创建与标记没有一一对应')
    with (root/'布局-61.extent.csv').open() as stream:
        final={r['文件'] for r in csv.DictReader(stream)}
    needed=set(names)&final
    restored=parsed((root/'verify.zenfs.log').read_text(),'Flush来源挂载文件：')
    restored_names={r['文件名'] for r in restored}
    require(restored_names==needed,'重挂载来源集合与末端实际有效文件不同')
    for row in restored:
        require(row['任务']==names[row['文件名']]['任务'] and row['标签']=='2','重挂载来源job或标签改变')
    require('Flush来源W标记：' not in (root/'verify.log').read_text(),'验证期错误产生新W来源')
    return dict(创建标记数=len(names),真实Flush创建数=len(expected),重挂载有效来源数=len(needed),
                原始标记=list(names.values()),重挂载记录=restored),names


def analyze(source, output):
    s=json.loads((source/'状态.json').read_text());a=json.loads((source/'阶段归档.json').read_text())
    require(a['完整'] and a['业务成功'] and not a['客机仍存活'] and s['当前客机PID'] is None,'来源未完整归档退出')
    require(json.loads((source/'Greedy重复核验.json').read_text())['通过'],'Greedy未重复通过')
    require(digest(Path(s['镜像处理']['archive']))==s['镜像处理']['archive_sha256'],'完整归档不同')
    initial=json.loads((source/'源码/受控标签来源.json').read_text())['标签']
    require(len(initial)==2 and set(initial.values())=={'H'},'初始来源不是仅H')
    reports={};layouts=[]
    for tag in ('p1','p2'):
        root=source/'逐臂采证'/tag;proof,names=verify_origins(root,initial)
        labels={**initial,**{n:'W' for n in names}}
        opportunity=opportunities(root,labels)
        verified=json.loads((source/(tag+'-核验.json')).read_text())
        for n in range(62):
            with (root/f'布局-{n}.extent.csv').open() as f:extents=list(csv.DictReader(f))
            hzones={e['Zone'] for e in extents if labels.get(e['文件'])=='H'}
            for zone in hzones:
                row=dict(臂=tag,布局=n,ZoneID=int(zone)//(128<<20))
                for role in ('H','W','普通'):
                    rows=[e for e in extents if e['Zone']==zone and labels.get(e['文件'],'普通')==role]
                    row[role+'字节']=sum(int(e['字节']) for e in rows)
                layouts.append(row)
        reports[tag]=dict(来源核验=proof,维护=verified['维护'],计时=timing(root),API延迟=verified['API延迟'],
            末端空间={k:v for k,v in verified['末端空间'].items() if k!='Zone'},机会=opportunity,
            实际改选次数=verified['改选次数'],
            影子差异数=sum(suggestion_difference(r['字段']) for r in verified['逐轮选择']),
            原日志SHA256={n:digest(root/n) for n in ('run.log','run.rocksdb.log','run.zenfs.log','verify.zenfs.log')})
    require(not output.exists(),'不覆盖派生结果');output.mkdir(parents=True)
    result=dict(来源=str(source),完整归档SHA256=s['镜像处理']['archive_sha256'],各臂=reports,
                分析源码SHA256=digest(Path(__file__)),
                边界='来源由创建时实际Flush上下文给出；寿命未知，混放不等于净收益；本轮仅Greedy重复。')
    (output/'来源与混放核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (output/'H所在Zone构成.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(layouts[0]));writer.writeheader();writer.writerows(layouts)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();x=analyze(args.source,args.output)
    print(json.dumps({t:dict(来源={k:v for k,v in r['来源核验'].items() if k not in ('原始标记','重挂载记录')},
        维护=r['维护'],正RR成本=r['机会']['正RR预计成本数'],候选随后RR=r['机会']['涉及独立RR数'],
        影子差异=r['影子差异数']) for t,r in x['各臂'].items()},ensure_ascii=False,indent=2))
