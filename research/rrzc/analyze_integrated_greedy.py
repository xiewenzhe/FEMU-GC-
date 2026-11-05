"""按三种完整配置拆分贡献；区分选区配置差异、实际维护差异和计时波动。"""
import argparse
import csv
import json
from pathlib import Path

from analyze_integrated_pool import REQUESTS
from analyze_pool_dbbench import repeat_guard,SEMANTIC
from plot_integrated_pool import render
from ycsb_af import digest,require


def signature(report):
    # 原始时间、日志行号和算法名称不是物理搬迁行为；原始比较另存，不覆盖。
    return [dict(类型=t['类型'],源Zone=t['Zone'],
        复制=[x['字段'] for x in t['复制']],映射提交=[x['字段'] for x in t['提交']],
        Reset=[x['字段'] for x in t['Reset']]) for t in report['事务']]


def analyze(root,prior,output,figures):
    state=json.loads((root/'状态.json').read_text());archive=json.loads((root/'阶段归档.json').read_text())
    require(state['当前客机PID'] is None and archive['完整'] and archive['业务成功'],'尚未归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    require(not output.exists() and not figures.exists(),'不覆盖分析或图表')
    repeat=json.loads((root/'协同Greedy重复核验.json').read_text());require(repeat['通过'],'本轮重复未通过')
    original=json.loads((prior/'完整实验分析/完整对照核验.json').read_text())
    require(original['通过'] and all(x['通过'] for x in original['重复'].values()),'来源未验收')
    current={t:json.loads((root/(t+'-核验.json')).read_text()) for t in ('p1','p2')}
    comparisons={}
    for n in (1,2):
        t='p'+str(n);m='m'+str(n);a=original['各臂'][m];b=current[t]
        raw=repeat_guard(prior/'逐臂采证'/m,root/'逐臂采证'/t,request_count=REQUESTS)
        decisions_equal=([{k:v for k,v in x['字段'].items() if k!='执行Greedy'} for x in a['逐轮选择']]==
                         [{k:v for k,v in x['字段'].items() if k!='执行Greedy'} for x in b['逐轮选择']])
        same=(raw['请求']['逻辑提交相同'] and all(raw['起点'][k]['相同'] for k in SEMANTIC)
            and raw['维护账目相同'] and raw['末端相同'] and raw['设备增量相同']
            and signature(a)==signature(b) and decisions_equal)
        comparisons[t]=dict(实际维护行为相同=same,迁移映射Reset签名相同=signature(a)==signature(b),
            候选决策字段除执行开关相同=decisions_equal,原始逐事件比较=raw,
            说明='跨算法的执行Greedy开关本来不同；原始比较不删除差异，另核对实际源目标和提交Reset')
    groups={'普通Greedy':[original['各臂'][t] for t in ('p1','p2')],
            '协同Greedy':[current[t] for t in ('p1','p2')],
            '协同模型':[original['各臂'][t] for t in ('m1','m2')]}
    output.mkdir();rows=[];lat=[]
    for name,reports in groups.items():
        for n,r in enumerate(reports,1):
            rows.append(dict(配置=name,重复=n,**r['维护'],**r['计时'],**r['资源'],改选次数=r['改选次数']))
            lat.extend(dict(配置=name,重复=n,**x) for x in r['API延迟'])
    for name,data in (('三组维护与吞吐.csv',rows),('三组API延迟.csv',lat)):
        with (output/name).open('x',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,list(data[0]));w.writeheader();w.writerows(data)
    result=dict(通过=True,来源=str(prior),本轮=str(root),三种配置=groups,模型贡献比较=comparisons,
        归档SHA256=state['镜像处理']['archive_sha256'],边界='同一种子工程重复，非统计显著性；计时与维护贡献分别报告')
    (output/'三组贡献核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    figures.mkdir()
    for name,baseline,mechanism,base_proof,proof,label in (
        ('放置贡献',groups['普通Greedy'],groups['协同Greedy'],original['重复']['Greedy'],repeat,'协同放置＋Greedy'),
        ('模型贡献',groups['协同Greedy'],groups['协同模型'],repeat,original['重复']['机制'],'协同放置＋模型')):
        data=dict(通过=True,各臂={**{'p'+str(n):r for n,r in enumerate(baseline,1)},
            **{'m'+str(n):r for n,r in enumerate(mechanism,1)}},重复=dict(Greedy=base_proof,机制=proof))
        source=figures/(name+'来源.json');source.write_text(json.dumps(data,ensure_ascii=False)+'\n')
        render(source,figures/name,mechanism_label=label,
            scope_note='跨时段运行存在明显耗时波动；延迟与吞吐只展示原值对比，不能据此归因放置或模型性能收益。')
    print(json.dumps({k:v['实际维护行为相同'] for k,v in comparisons.items()},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','prior','output','figures'):p.add_argument(n,type=Path)
    a=p.parse_args();analyze(a.root,a.prior,a.output,a.figures)
