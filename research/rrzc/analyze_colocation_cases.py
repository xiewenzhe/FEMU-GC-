"""逐请求核验三种协同布局情形；读取实际日志，不把预期收益作为通过条件。"""
import csv
import hashlib
import json
import re
from pathlib import Path
from analyze_reproducibility import accounting
from audit_colocation_transactions import audit as audit_transactions

MiB=1<<20
H,W,C='/joint/H.sst','/joint/W.sst','/joint/C.sst'
CASES=('w-first','rr-first','cooled')


def table(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream))


def logical_requests(root):
    data=table(root/'请求.csv');assert len(data)%2==0,'请求日志未闭合'
    keys=('序号','阶段','操作','文件','偏移','字节','参数')
    requests=[]
    for n in range(0,len(data),2):
        a,b=data[n:n+2]
        assert a['状态']=='提交' and b['状态']=='完成'
        assert all(a[k]==b[k] for k in keys) and int(a['序号'])==n//2
        assert int(a['纳秒'])<=int(b['纳秒'])
        requests.append(tuple(a[k] for k in keys))
    return requests


def frames(root):
    rows=table(root/'空间与设备.csv')
    return {r['阶段']:{k:int(v) for k,v in r.items() if k!='阶段'} for r in rows}


def normalize_blocks(root,stage):
    start={(r['Zone'],r['块']):int(r['代际']) for r in table(root/'start.blocks.csv')}
    out=[]
    for r in table(root/(stage+'.blocks.csv')):
        if int(r['Zone'])<3:continue
        v={k:int(r[k]) for k in ('Zone','块','Channel','LUN','Plane','物理块','RC','十秒增量','三十秒增量','标志')}
        v['代际相对起点']=int(r['代际'])-start[r['Zone'],r['块']]
        out.append(v)
    assert len(out)==61*128 and max(r['RC'] for r in out)<=1024
    return out


def business_log(root):
    text=(root/'zenfs.log').read_text()
    begin='受控放置业务开始：';end='受控放置业务截止：'
    assert text.count(begin)==text.count(end)==1,'业务边界缺失或重复'
    return text[text.index(begin):text.index(end)]


def arm(root,scenario,k):
    assert (root/'wrapper.exit').read_text().strip()=='0'
    text=(root/'zenfs.log').read_text();run=(root/'run.log').read_text()
    assert f'三情形受控验证完成：{scenario}，K={k}' in run
    assert '受控GC批次检查：启用=1，原启动水平=20%' in text
    assert '启动 Zone 维护线程：GC=1，主机端 RR=1' in text
    assert '[ERROR]' not in text and '失败' not in run
    assert [int(i) for i in re.findall(r'受控GC检查点开始：批次=(\d+)',text)]==[1,2,3]
    assert (root/'受控GC.ack').read_text().strip()=='3'
    assert '受控GC紧急检查：' not in text
    layout={r['文件']:r for r in table(root/'prepared.extents.csv')}
    h,w,c=(int(layout[n]['Zone']) for n in (H,W,C))
    assert (h==w and h!=c) if k else (h!=w and h==c)
    assert {name:int(layout[name]['字节']) for name in (H,W,C)}=={H:32*MiB,W:96*MiB,C:96*MiB}
    assert all(int(r['RC'])==0 for r in table(root/'prepared.blocks.csv') if int(r['Zone'])>=3)
    points=frames(root);prepared=points['prepared'];final=points['business']
    assert prepared['有效']==6880*MiB and prepared['空闲']==928*MiB
    assert final['有效']==6784*MiB and final['空闲']==1024*MiB and not final['满区可回收']
    assert all(r['采样Open']<=8 and r['采样Active']<=12 and r['会话历史最高RC']<=1024
               for r in points.values())
    assert all(r['文件']!=W for r in table(root/'business.extents.csv'))
    ledger=accounting(root,'zenfs.log')
    assert ledger['复制日志与维护汇总相同'] and not ledger['映射提交失败数']
    assert ledger['ZC累计账目闭合'] and ledger['RR累计账目闭合']
    # 本入口实际执行单区Greedy，其集合标记不同于旧模型的“统一决策”。
    tx=audit_transactions(root)
    assert tx['通过'] and tx['维护账目']==ledger
    phases=business_log(root)
    assert ledger['总迁移字节']==sum(int(x) for x in
        re.findall(r'V1后续迁移复制：[^\n]*有效字节=(\d+)，复制成功=1',phases)), '维护发生在业务窗之外'
    copy_rows=[]
    for t in tx['事务']:
        for item in t['复制']:
            f=item['字段'];copy_rows.append(dict(类型=t['类型'],文件=f['文件名'],
                字节=int(f['有效字节']),源Zone=int(f['源Zone'])//(128*MiB),
                目标Zone=int(f['目标Zone'])//(128*MiB)))
    request=logical_requests(root)
    reads=sum(r[2]=='READ' and r[1]=='业务' for r in request)
    assert reads==(700 if scenario=='cooled' else 1212)
    return dict(通过=True,场景=scenario,K=k,维护=ledger,空间=points,完整事务核验=True,
        按文件复制=tx['按文件复制'],
        H所在Zone=h,W所在Zone=w,C所在Zone=c,迁移片段=copy_rows,
        请求数=len(request),业务热读次数=reads,
        请求摘要=hashlib.sha256(json.dumps(request,ensure_ascii=False).encode()).hexdigest(),
        源日志SHA256=hashlib.sha256((root/'zenfs.log').read_bytes()).hexdigest())


def compare_repeats(a,b):
    # 元数据时间戳与会话累计量不冒称相同；核验实际数据状态、输入和维护路径。
    inputs=logical_requests(a)==logical_requests(b)
    initial=normalize_blocks(a,'prepared')==normalize_blocks(b,'prepared')
    final=normalize_blocks(a,'business')==normalize_blocks(b,'business')
    extents=all(table(a/(p+'.extents.csv'))==table(b/(p+'.extents.csv')) for p in ('prepared','business'))
    zones=all(table(a/(p+'.zones.csv'))[3:]==table(b/(p+'.zones.csv'))[3:] for p in ('prepared','business'))
    log=lambda root:[r for r in business_log(root).splitlines() if any(x in r for x in
        ('V1后续迁移复制：','V1后续映射提交：','简单双因素实际集合：'))]
    # 维护决策及复制字段逐条相同，丢弃时间/线程前缀。
    content=lambda rows:[r.split(' ',2)[2] for r in rows]
    events=content(log(a))==content(log(b))
    delta=lambda root:{k:frames(root)['business'][k]-frames(root)['prepared'][k]
        for k in ('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')}
    counters=delta(a)==delta(b)
    return dict(通过=inputs and initial and final and extents and zones and events and counters,
        请求相同=inputs,数据区起点相同=initial,数据区终态相同=final,
        有效布局相同=extents,数据Zone状态相同=zones,维护字段相同=events,业务设备增量相同=counters,
        边界='实际数据区逐块和事件核验；不把元数据时钟或设备会话累计数宣称完全相同')


def analyze(output,report_dir=None):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None
    source=output/state['标签'];results={};pairs={}
    for scenario in CASES:
        arms={}
        for k in (0,1):
            tags=[f'{scenario}-k{k}-r{r}' for r in (1,2)]
            arms[k]=[arm(source/tag,scenario,k) for tag in tags]
            repeat=compare_repeats(*(source/tag for tag in tags))
            assert repeat['通过'],f'{scenario} K={k}重复未通过：{repeat}'
            results[f'{scenario}-k{k}']=dict(各轮=arms[k],重复=repeat)
        assert logical_requests(source/f'{scenario}-k0-r1')==logical_requests(source/f'{scenario}-k1-r1'), \
            '放置对照逻辑输入不同'
        a,b=arms[0][0],arms[1][0]
        assert all(a['空间']['business'][k]==b['空间']['business'][k] for k in ('空闲','有效','满区可回收'))
        difference=b['维护']['总迁移字节']-a['维护']['总迁移字节']
        pairs[scenario]=dict(Greedy普通放置=a['维护'],Greedy协同放置=b['维护'],
            总迁移差字节=difference,同工作量及终态空间通过=True,
            终态采样Active=[a['空间']['business']['采样Active'],b['空间']['business']['采样Active']],
            终态边界='有效/空闲/满区可回收量相同，不等于物理布局及Active资源状态相同',
            结论='本情形减少维护复制' if difference<0 else '本情形增加维护复制' if difference>0 else '本情形未减少维护复制')
    result=dict(通过=True,各组=results,对照=pairs,
        原始结果目录=str(output.resolve()),分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        边界='同一Greedy，显式受控分类和检查边界，文件级机制实验；不代表在线识别、模型选区或正常并发性能收益')
    dest=report_dir if report_dir is not None else output
    if report_dir is not None:dest.mkdir(parents=True,exist_ok=False)
    assert not (dest/'三情形核验.json').exists() and not (dest/'阶段三分析.md').exists(),'不覆盖已有分析'
    (dest/'三情形核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 固定K=1三情形实际结果','',
        '|情形|K0 ZC/RR|K1 ZC/RR|K0迁移MiB|K1迁移MiB|K1减K0 MiB|',
        '|---|---:|---:|---:|---:|---:|']
    for scenario,p in pairs.items():
        a,b=p['Greedy普通放置'],p['Greedy协同放置']
        lines.append(f'|{scenario}|{a["ZC次数"]}/{a["RR次数"]}|{b["ZC次数"]}/{b["RR次数"]}|'
                     f'{a["总迁移字节"]/MiB:.3f}|{b["总迁移字节"]/MiB:.3f}|{p["总迁移差字节"]/MiB:.3f}|')
    lines+=['','两次重复均核验请求、数据区起点/终态、维护字段和业务设备增量。'
        '每组业务结束有效6784MiB、空闲1024MiB，报告的是本受控窗口，不能推广为长期稳定收益。',
        '普通放置和协同放置的终态Active采样不同，空间总量相同不等于完整终态相同。',
        '独立事务核验逐笔关联实际Greedy集合/ RR开始、复制、映射提交、源Reset及维护汇总。','',result['边界']]
    (dest/'阶段三分析.md').write_text('\n'.join(lines)+'\n')
    return result
