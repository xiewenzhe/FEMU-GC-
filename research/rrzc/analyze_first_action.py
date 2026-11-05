"""独立整数重算首区折扣、评分及实际执行；不把效用金额称真实RR节省。"""
import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
import shutil

from analyze_all_blocks_dbbench import fields
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from analyze_pool_tail_stream import phase_arm
from ycsb_af import digest,require

NS=10**9
MAX=(1<<64)-1


def decisions(root,_):
    rows=defaultdict(list);files=defaultdict(list);suggestions={};actual={}
    for number,line in enumerate((root/'zenfs.log').read_text().splitlines(),1):
        for marker,dest in (('首区文件：',files),('首区候选：',rows)):
            if marker in line:
                r={k:int(v) for k,v in fields(line,marker).items()};r['行']=number
                dest[r['轮次']].append(r)
        for marker,dest in (('首区建议：',suggestions),('共同观测实际决策：',actual)):
            if marker in line:
                r={k:int(v) for k,v in fields(line,marker).items()};n=r['轮次']
                require(n not in dest,'首区决策重复');dest[n]=r
    require(set(suggestions)==set(actual),'首区建议或实际记录缺失')
    result=[]
    for n,a in actual.items():
        candidates=rows[n];by_zone={r['Zone']:r for r in candidates}
        require(len(by_zone)==len(candidates),'同轮候选重复')
        for z,r in by_zone.items():
            own=[f for f in files[n] if f['Zone']==z];credit=hot=covered=0
            for f in own:
                scale=min(f['存活纳秒'],30*NS)
                amount=f['区内字节']*scale//(scale+r['ETA']) if (
                    f['热读'] and f['存活纳秒']>=2*NS and r['ETA']!=MAX) else 0
                require(amount==f['抵扣'],'单文件折扣算术不同')
                credit+=amount;covered+=f['区内字节'];hot+=f['区内字节']*f['热读']
            if not r['风险可知']:credit=0
            require(credit==r['抵扣'] and covered==r['覆盖'] and hot==r['热字节']
                and r['净复制']==r['有效']-credit,'首区金额或快照覆盖不符')
            require(not r['安全'] or covered==r['有效'],'安全候选覆盖不完整')
            if r['风险可知'] and r['RC']<r['阈值']:
                if not r['近期增量'] or not r['十秒增量']:eta=MAX
                else:
                    margin=r['阈值']-r['RC'];span=min(r['代际年龄'],10*NS)
                    eta=max((margin*span+r['十秒增量']-1)//r['十秒增量'],
                        (margin*r['近期间隔']+r['近期增量']-1)//r['近期增量'])
                require(r['ETA']==eta,'代表块ETA独立重算不同')
        safe=[r for r in candidates if r['安全'] and 0<r['有效'] and r['回收']>0 and r['抵扣']<=r['有效']]
        best=min(safe,key=lambda r:(Fraction(r['净复制'],r['回收']),r['有效'],r['Zone'])) if safe else None
        greedy=min(safe,key=lambda r:(r['有效'],r['Zone'])) if safe else None
        s=suggestions[n]
        require(s['存在']==int(best is not None) and s['Zone']==(best['Zone'] if best else 0)
            and s['补区计划']==0,'新模型建议不符合直接评分')
        require(a['模型存在']==s['存在'] and a['模型Zone']==(best['Zone'] if best else MAX)
            and a['Greedy存在']==int(greedy is not None) and a['GreedyZone']==(greedy['Zone'] if greedy else 0),
            '共同候选或模型建议不同')
        chosen=greedy if a['执行Greedy'] else best
        require(a['实际存在']==int(chosen is not None) and a['实际Zone']==(chosen['Zone'] if chosen else 0),
            '实际选区与执行开关不符')
        result.append(dict(字段=a,候选=candidates,文件=files[n],候选方案=[],空间需求={},
            改选=chosen is not None and chosen['Zone']!=greedy['Zone']))
    return result


def finish(root,output):
    require(not output.exists(),'不覆盖原首区分析')
    state=json.loads((root/'状态.json').read_text());stage=json.loads((root/'阶段归档.json').read_text())
    require(state.get('完整实验完成') and state['当前客机PID'] is None and stage['完整']
        and stage['业务成功'] and not stage['客机仍存活'],'实验未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    reports={t:json.loads((root/(t+'-核验.json')).read_text()) for t in ('p1','p2','m1','m2')}
    cross={t:dict(起点=initial(root/'逐臂采证/p1',root/'逐臂采证'/t),
        请求=request_comparison(root/'逐臂采证/p1',root/'逐臂采证'/t)) for t in ('p2','m1','m2')}
    require(all(all(x['起点'][k]['相同'] for k in SEMANTIC) and x['请求']['逻辑提交相同']
        and x['请求']['完整成功请求数']==[4521984]*2 for x in cross.values()),'完整起点或请求不同')
    repeats={n:json.loads((root/(n+'重复核验.json')).read_text()) for n in ('Greedy','模型')}
    require(repeats['Greedy']['通过'],'新配置Greedy重复未通过')
    phases={t:phase_arm(root/'逐臂采证'/t,r) for t,r in reports.items()}
    output.mkdir(parents=True)
    result=dict(通过=True,来源=str(root),各臂=reports,阶段=phases,跨臂=cross,重复=repeats,
        归档SHA256=state['镜像处理']['archive_sha256'],分析器SHA256=digest(Path(__file__)),
        边界='固定种子受控单前台两次工程重复；时间型模型输入可以波动；不宣称正常并发或统计显著性')
    (output/'完整核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(Path(__file__),output/'分析源码.py')
    lines=['# 首区净成本模型完整24窗口对照','',result['边界'],'',
        '|指标|Greedy1|Greedy2|模型1|模型2|','|---|---:|---:|---:|---:|']
    for label,group,key,scale in [('总迁移MiB','维护','总迁移字节',1<<20),
        ('ZC迁移MiB','维护','ZC迁移字节',1<<20),('RR迁移MiB','维护','RR迁移字节',1<<20),
        ('ZC次数','维护','ZC次数',1),('RR次数','维护','RR次数',1),('成功Reset','维护','全部成功Reset次数',1),
        ('末端空闲MiB','末端空间','空闲字节',1<<20),('末端有效MiB','末端空间','有效字节',1<<20),
        ('吞吐ops/s','计时','请求窗口吞吐ops',1)]:
        lines.append('|'+label+'|'+'|'.join(f'{r[group][key]/scale:.3f}' for r in reports.values())+'|')
    lines+=['','改选次数：'+str({t:r['改选次数'] for t,r in reports.items()})+'。',
        'API平均及P99/P99.9/P99.99保留在完整核验.json。无WAL Put不是介质编程耗时。',
        '首区文件抵扣为按观察存活时长折扣的热文件效用，不是未来RR概率或已避免字节。'
        '每次真实改选的候选、文件、块身份、ETA及分数保留；是否有净收益按总复制和末端空间判断。',
        '模型重复若不同，逐臂报告，不用平均掩盖；未因负收益调整窗口、候选或负载。']
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();finish(a.root,a.output)
