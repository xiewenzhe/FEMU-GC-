"""核对固定H实验的真实邻居和跨层合并，不将原始同组文件当作当前邻居。"""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import re

from pool_dbbench_guest import table,key
from ycsb_af import digest,require

ZONE=128<<20


def events(path):
    rows=[]
    for line,text in enumerate(path.read_text().splitlines(),1):
        if 'EVENT_LOG_v1 ' not in text:continue
        record=json.loads(text.split('EVENT_LOG_v1 ',1)[1])
        rows.append(dict(行=line,记录=record))
    return rows


def arm(root,proof,labels):
    original={r['文件']:r for r in table(root/'布局-0.sst.csv')}
    current={r['文件']:r for r in table(root/'布局-52.sst.csv')}
    final={r['文件']:r for r in table(root/'布局-61.sst.csv')}
    extents=table(root/'布局-52.extent.csv')
    h0={name for name,r in original.items() if key(r['最小键十六进制'])<4096}
    hot_zones={int(e['Zone'])//ZONE for e in extents if e['文件'].removeprefix('/rrzc-dbbench') in h0}
    require(hot_zones=={7},'本诊断针对已定位的第52检查点Zone7，实际映射改变则停止')
    neighbors=[]
    for e in extents:
        if int(e['Zone'])//ZONE not in hot_zones:continue
        name=e['文件'].removeprefix('/rrzc-dbbench');r=current[name]
        neighbors.append(dict(文件=name,标签=labels.get(e['文件'],'未分类'),层级=int(r['层级']),
            最小键=key(r['最小键十六进制']),最大键=key(r['最大键十六进制']),Zone内字节=int(e['字节'])))
    require(sum(e['Zone内字节'] for e in neighbors)==41706990,'实际邻居字节与已定位有效量不符')
    require({e['文件'] for e in neighbors}==h0|{'/000197.sst','/000198.sst','/000199.sst'},'邻居集合改变')
    records=events(root/'run.rocksdb.log')
    starts=[r for r in records if r['记录'].get('event')=='compaction_started']
    finished=[r for r in records if r['记录'].get('event')=='compaction_finished']
    require(Counter(r['记录']['job'] for r in starts)==Counter(r['记录']['job'] for r in finished),
        '存在未闭合Compaction，不能判断为正常完成后的残留')
    l3_inputs={number for r in starts for number in r['记录'].get('files_L3',[])}
    deleted={r['记录']['file_number'] for r in records if r['记录'].get('event')=='table_file_deletion'
        and r['记录']['status']=='OK'}
    w0={name for name,r in original.items() if key(r['最小键十六进制'])<65536 and
        labels.get('/rrzc-dbbench'+name)=='W'}
    retained=sorted(w0&set(final));retired=sorted(w0-set(final))
    require(len(retained)==7 and len(retired)==23,'原W0存活集合与已验收轨迹不同')
    def file_state(name):
        r=original[name];low,high=key(r['最小键十六进制']),key(r['最大键十六进制'])
        number=int(name.removeprefix('/').removesuffix('.sst'))
        locations=[dict(Zone=int(e['Zone'])//ZONE,字节=int(e['字节'])) for e in extents
            if e['文件'].removeprefix('/rrzc-dbbench')==name]
        newer=[dict(文件=n,层级=int(x['层级']),最小键=key(x['最小键十六进制']),最大键=key(x['最大键十六进制']))
            for n,x in final.items() if n not in original and int(x['层级'])<int(r['层级']) and
            key(x['最小键十六进制'])<=high and key(x['最大键十六进制'])>=low]
        return dict(文件=name,原层级=int(r['层级']),最小键=low,最大键=high,本轮被选为L3输入=number in l3_inputs,
            本轮成功删除=number in deleted,第52检查点片段=locations,末端同键范围新生成上层SST=newer)
    retained_states=[file_state(n) for n in retained]
    w5_states=[file_state('/000197.sst'),file_state('/000198.sst'),file_state('/000199.sst')]
    require(all(not e['本轮被选为L3输入'] and not e['本轮成功删除'] and e['末端同键范围新生成上层SST']
        for e in retained_states+w5_states),'旧文件残留或新版本证据不完整')
    require(all(int(n.removeprefix('/').removesuffix('.sst')) in l3_inputs&deleted for n in retired),
        '23个失效W0缺少真正L3合并及成功删除证据')
    levels=defaultdict(lambda:dict(文件数=0,字节=0))
    for r in final.values():
        levels[r['层级']]['文件数']+=1;levels[r['层级']]['字节']+=int(r['字节'])
    text=(root/'run.rocksdb.log').read_text()
    def option(name):
        matches=re.findall(r'Options\.'+re.escape(name)+r': ([^\s]+)',text)
        require(len(matches)==1,'配置不唯一：'+name);return matches[0]
    base=int(option('max_bytes_for_level_base'));multiplier=float(option('max_bytes_for_level_multiplier'))
    require(option('level_compaction_dynamic_level_bytes')=='0','本诊断要求静态分层容量')
    require(multiplier==10 and base==134217728,'分层配置不同')
    l2_target=int(base*multiplier)
    last_rr=proof['真实RR'][-1]
    require(last_rr['最近同代际GC观察']['资格']['实际比例资格']==0,'末次RR资格链不同')
    return dict(第52检查点H0邻居=neighbors,原W0仍存活=retained_states,实际邻居W5=w5_states,
        原W0已退役=retired,闭合Compaction任务=len(starts),真正L2合并L3任务=sum('files_L3' in r['记录'] for r in starts),
        L3合并输入文件=sorted(l3_inputs),末端分层=dict(levels),L2静态目标字节=l2_target,
        L2末端容量比=levels['2']['字节']/l2_target,合并优先级配置=option('compaction_pri'),
        最近GC观察=last_rr['最近同代际GC观察'],最后RR=last_rr,
        输入哈希={name:digest(root/name) for name in ('run.rocksdb.log','布局-0.sst.csv','布局-52.sst.csv','布局-52.extent.csv','布局-61.sst.csv')})


def analyze(results,output):
    state=json.loads((results/'状态.json').read_text());archive=json.loads((results/'阶段归档.json').read_text())
    proof_path=results/'生命周期机会核验.json';proof=json.loads(proof_path.read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and
        state['当前客机PID'] is None and proof['通过'] and proof['重复']['通过'],'原实验未完整验收退出')
    require(digest(Path(state['镜像处理']['archive']))==proof['完整归档SHA256'],'完整归档哈希不同')
    require(not output.exists(),'不覆盖分析输出')
    labels=json.loads((results/'源码/受控标签来源.json').read_text())['标签']
    reports={tag:arm(results/'逐臂采证'/tag,proof['各臂'][tag],labels) for tag in ('p1','p2')}
    output.mkdir(parents=True)
    for tag,report in reports.items():
        with (output/(tag+'-H0实际邻居.csv')).open('x',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(report['第52检查点H0邻居'][0]));writer.writeheader()
            writer.writerows(report['第52检查点H0邻居'])
    result=dict(通过=True,各臂=reports,来源=str(results),机会核验SHA256=digest(proof_path),
        原始完整归档SHA256=proof['完整归档SHA256'],分析源码SHA256=digest(Path(__file__)),
        结论='W0残留在Zone8/9；H0当前邻居是W5。单独淘汰W0残留不能改变H0 Zone有效量。',
        限制='容量比是按实际静态配置计算的末端估算，不是当前运行的Picker内部评分；未采集内部所有候选排序。')
    (output/'实际邻居与Compaction核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();result=analyze(args.results,args.output)
    print(json.dumps({tag:{k:a[k] for k in ('闭合Compaction任务','真正L2合并L3任务','L2末端容量比')}
        for tag,a in result['各臂'].items()},ensure_ascii=False,indent=2))
