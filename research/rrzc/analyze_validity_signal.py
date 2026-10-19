"""验证过去有效量趋势与后续SST删除的关联；不预测寿命、不训练或执行新策略。"""
import hashlib,json
from collections import Counter,defaultdict
from pathlib import Path
from analyze_rocksdb_observe import past_window,identity,delete_label,SECOND
from analyze_rr_opportunities import stable,adjacent_rate
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require

LABEL='validity-signal-20261017'
STATES=('未下降','单次下降','多次下降')

def mapped_parts(z,parts):
    fragments=parts.get((z['批次'],z['Zone']),[])
    keys={(f['文件编号'],f['文件'],f['片段起点'],f['区内有效字节']) for f in fragments}
    complete=(len(keys)==len(fragments) and bool(keys) and
        sum(int(f['区内有效字节']) for f in fragments)==int(z['有效字节'])==int(z['映射覆盖字节']) and
        all(f['文件'].endswith('.sst') for f in fragments))
    return complete,keys

def trend(history,current,seconds,parts):
    """输入只含当时已完成的快照；事后迁移审计和删除标签不进入本函数。"""
    result=past_window(history,current,seconds)
    if not result['可知']:return result
    cutoff=int(current['查询开始纳秒'])
    available=[r for r in history if int(r['查询结束纳秒'])<cutoff]
    anchor=[r for r in available if int(r['查询结束纳秒'])<=cutoff-seconds*SECOND][-1]
    segment=[r for r in available if int(r['查询结束纳秒'])>=int(anchor['查询结束纳秒'])]+[current]
    previous=None
    for z in segment:
        complete,current_parts=mapped_parts(z,parts)
        if not stable(z) or not complete:
            return dict(可知=False,原因='窗口含不稳定或不完整SST映射')
        if previous is not None and not current_parts.issubset(previous):
            return dict(可知=False,原因='满区映射新增、移动或片段形状变化')
        previous=current_parts
    decreases=sum(int(a['有效字节'])>int(b['有效字节']) for a,b in zip(segment,segment[1:]))
    return dict(可知=True,分类=STATES[min(decreases,2)],净减少字节=result['净减少字节'],
        下降区间数=decreases,实际区间秒=result['实际区间秒'],
        边界='多次仅指至少两个下降采样区间，不等于持续线性下降；本趋势自身尚未证明下降不是迁移，且不输出剩余寿命。')

def migration_zones(lines):
    """事后整轮保守审计，不参与趋势计算；按地址排除整轮所有代际。"""
    touched=set()
    for line in lines:
        if 'V1后续迁移复制：' in line:
            copy=fields(line,'V1后续迁移复制：')
            touched.update((copy['源Zone'],copy['目标Zone']))
    return touched

def future_deletions(z,parts,deleted,end):
    by_name=defaultdict(int)
    for p in parts[z['批次'],z['Zone']]:by_name[p['文件']]+=int(p['区内有效字节'])
    labels=[(size,delete_label(path,int(z['查询结束纳秒']),end,deleted)) for path,size in by_name.items()]
    complete=all(label['观察完整'] for _,label in labels)
    return dict(完整十秒删除观察=complete,
        十秒内成功删除SST字节=sum(size for size,label in labels if label['十秒内成功删除']) if complete else None,
        边界='仅为当前映射SST的成功删除事件；不等于句柄释放、整区失效、无RR反事实或已节省搬运。')

def groups(records):
    result={state:dict(观察数=0,删除窗口完整数=0,随后十秒有SST删除数=0,随后十秒无SST删除数=0,末尾截断数=0) for state in STATES}
    for r in records:
        group=result[r['事前趋势']['分类']];group['观察数']+=1;label=r['事后标签']
        if not label['完整十秒删除观察']:group['末尾截断数']+=1;continue
        group['删除窗口完整数']+=1
        group['随后十秒有SST删除数' if label['十秒内成功删除SST字节']>0 else '随后十秒无SST删除数']+=1
    return result

def inspect(zones,mapping,events,lines):
    parts=defaultdict(list)
    for p in mapping:parts[p['批次'],p['Zone']].append(p)
    deleted=defaultdict(list)
    for e in events:
        if e['事件']=='SST删除' and e['成功']=='1':deleted[e['文件']].append(int(e['经过纳秒']))
    end=max(int(z['查询结束纳秒']) for z in zones);touched=migration_zones(lines)
    histories=defaultdict(list);records=[];unknown=Counter();first={}
    for z in zones:
        h=histories[z['Zone']];old=h[-1] if h else None
        if z['阶段']=='1' and 0<int(z['有效字节']) and z['剩余字节']=='0':
            complete,_=mapped_parts(z,parts)
            if stable(z) and complete:
                scopes=['所有满区SST']
                if int(z['容量字节'])-int(z['有效字节'])>=192<<20:
                    rate=adjacent_rate(old,z)
                    if int(z['RC'])>=4096:scopes+=['基础回收资格_已达阈值']
                    elif rate['可知'] and rate['预测纳秒'] is not None and rate['预测纳秒']<=10*SECOND:
                        scopes+=['基础回收资格_阈值前近期风险']
                for window in (10,30):
                    signal=trend(h,z,window,parts)
                    if not signal['可知']:
                        unknown[str(window)+'秒_'+signal['原因']]+=1;continue
                    for scope in scopes:
                        r=dict(窗口秒=window,范围=scope,批次=z['批次'],Zone=z['Zone'],
                            区代际身份=list(identity(z)),当前RC=int(z['RC']),当前有效字节=int(z['有效字节']),
                            事前趋势=signal,事后标签=future_deletions(z,parts,deleted,end),
                            事后审计_整轮从未迁移触及=z['Zone'] not in touched)
                        records.append(r)
                        key=(window,scope,identity(z))
                        if key not in first:first[key]=r
        h.append(z)
    counts={}
    scopes=('所有满区SST','基础回收资格_已达阈值','基础回收资格_阈值前近期风险')
    for window in (10,30):
        counts[str(window)]={}
        for scope in scopes:
            all_rows=[r for r in records if r['窗口秒']==window and r['范围']==scope]
            first_rows=[r for (w,s,_),r in first.items() if w==window and s==scope]
            counts[str(window)][scope]=dict(全部重复快照=groups(all_rows),
                每区代际首个可用观察=groups(first_rows),
                事后无迁移审计子集_重复快照=groups([r for r in all_rows if r['事后审计_整轮从未迁移触及']]),
                事后无迁移审计子集_首个观察=groups([r for r in first_rows if r['事后审计_整轮从未迁移触及']]))
    return dict(分组=counts,趋势未知原因=dict(unknown),首个观察明细=list(first.values()),
        迁移曾触及Zone地址=sorted(touched,key=int),
        说明=[
            '窗口固定10／30秒，分类固定为零次／一次／至少两次下降；没有扫描阈值、加权、训练或拟合失效时间。',
            '首个观察按每个范围、窗口和区代际仅取首个事前趋势可知样本，不按未来结果挑选；不同窗口、范围及实验仍相关。',
            '基础资格只含满区、SST完整覆盖、至少192MiB逻辑回收和当前风险，不含实际GC门槛、空目标、相对Greedy预算或尾部复核，不是实际可改选机会。',
            '整轮无迁移审计使用未来也可能出现的维护信息，且按地址保守排除全部代际，会有选择偏差；仅验证污染，不可输入选择器或作为预测准确率。',
            '原始趋势与事后审计分栏；未下降不等于长期有效，SST可能整份突然退役；成功删除不等于整个Zone马上失效。'])

def audit(results):
    from analyze_pair_rate import audit as pair_audit
    from analyze_rocksdb_anchor_slow import audit as slow_audit
    from analyze_rocksdb_guard_pair import audit as guard_audit
    source_pair=results/'rocksdb-pair-rate-20261016'
    pair=pair_audit(source_pair);require(pair==json.loads((source_pair/'独立核验.json').read_text()) and pair['维护无错误'],'旧双点完整核验不符')
    source_slow=results/'rocksdb-anchor-slow-20261016'
    require(slow_audit(source_slow)==json.loads((source_slow/'机会分类复核.json').read_text()),'旧限速核验不符')
    source_guard=results/'rocksdb-guard-pair-20261016'
    require(guard_audit(source_guard)==json.loads((source_guard/'配对核验.json').read_text()),'旧执行配对核验不符')
    inputs=[('双点Greedy',source_pair/'运行归档'/source_pair.name),
        ('限速Greedy',source_slow/'运行归档'/source_slow.name),
        *[(mode,source_guard/mode/'运行归档'/'rocksdb-anchor-slow-20261016') for mode in ('greedy','guarded')]]
    output={}
    for label,run in inputs:
        data=inspect(rows(run/'zones.csv'),rows(run/'files.csv'),rows(run/'events.csv'),(run/'zenfs.run.log').read_text().splitlines())
        data['来源']=str(run)
        data['输入身份']={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('zones.csv','files.csv','events.csv','zenfs.run.log')}
        output[label]=data
    return dict(原完整归档复算一致=True,固定四份完整观察=output,
        分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        接入生产=False,新增设备实验=False,
        结论边界='这是信号覆盖和污染审计，不是已实现寿命预测、因果效应或新机制收益。缺少证据时保持未知，不据相关快照直接开启新准入。')

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要已有results目录');results=Path(sys.argv[1]).resolve()
    output=results/LABEL;require(not output.exists(),'新分析目录已存在，拒绝覆盖')
    report=audit(results);output.mkdir()
    (output/'有效量趋势验证.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('固定四份完整归档与有效量趋势审计完成；未修改生产机制或启动新设备实验。')
