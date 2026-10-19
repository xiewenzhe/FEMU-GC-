"""真实RocksDB事前信号覆盖与事后标签；不拟合预测器、不改变victim选择。"""
import hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from analyze_stream import rows
from analyze_periodic import latency
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_v1_shadow_stream import require,fields
from guarded_rounds import parse

LABEL='rocksdb-observe-20261016'
SECOND=10**9
MIB=1<<20

def identity(r,block=False):
    keys=['Zone','会话高位','会话低位','设备代际后','主机代际后','主机序号后']
    if block:keys+=['块编号']
    return tuple(r[k] for k in keys)

def past_window(history,current,seconds,block=False):
    """仅使用本次查询之前已经返回的数据；身份变化、空档或活动写入保守未知。"""
    require(seconds in (2,10,30),'不是固定过去窗口')
    cutoff=int(current['查询开始纳秒']);available=[r for r in history if int(r['查询结束纳秒'])<cutoff]
    before=[r for r in available if int(r['查询结束纳秒'])<=cutoff-seconds*SECOND]
    if not before:return dict(可知=False,原因='缺完整过去窗口')
    old=before[-1];segment=[r for r in available if int(r['查询结束纳秒'])>=int(old['查询结束纳秒'])]+[current]
    if cutoff-int(old['查询结束纳秒'])>(seconds+2)*SECOND:return dict(可知=False,原因='端点过旧')
    if any(r['统计稳定']!='1' or identity(r,block)!=identity(current,block) for r in segment):
        return dict(可知=False,原因='会话、Reset、最大块或查询稳定性改变')
    if any(int(b['查询结束纳秒'])-int(a['查询结束纳秒'])>2500000000 for a,b in zip(segment,segment[1:])):
        return dict(可知=False,原因='采样空档超过2.5秒')
    if block:
        changes=[int(b['RC'])-int(a['RC']) for a,b in zip(segment,segment[1:])]
        if any(v<0 for v in changes):return dict(可知=False,原因='同块RC倒退')
        span=int(current['设备快照纳秒'])-int(old['设备快照纳秒'])
        require(span>0,'同块设备时钟不递增')
        return dict(可知=True,同块真实读取增量=int(current['RC'])-int(old['RC']),
            实际区间秒=span/SECOND,各采样间隔均有读取=all(v>0 for v in changes))
    if any(int(r['剩余字节'])!=0 for r in segment):return dict(可知=False,原因='窗口含可写区，不以净变化推断退役')
    changes=[int(b['有效字节'])-int(a['有效字节']) for a,b in zip(segment,segment[1:])]
    if any(v>0 for v in changes):return dict(可知=False,原因='窗口有效量增加或快照交错')
    return dict(可知=True,净减少字节=int(old['有效字节'])-int(current['有效字节']),
        实际区间秒=(int(current['查询结束纳秒'])-int(old['查询结束纳秒']))/SECOND)

def delete_label(path,cutoff,end,deleted):
    """未见删除不等于不会失效；观察结束不足10秒时明确截断。"""
    future=[t for t in deleted.get(path,[]) if cutoff<t<=cutoff+10*SECOND]
    if future:return dict(观察完整=True,十秒内成功删除=True,距删除秒=(future[0]-cutoff)/SECOND)
    if end<cutoff+10*SECOND:return dict(观察完整=False,十秒内成功删除=None,原因='末尾观察不足十秒')
    return dict(观察完整=True,十秒内成功删除=False,原因='完整窗口未见成功删除，不等于未来不会失效')

def features(zones,mapping,live,events):
    created={};deleted=defaultdict(list)
    for e in events:
        if e['成功']!='1':continue
        if e['事件']=='SST创建':created[e['文件']]=int(e['经过纳秒'])
        if e['事件']=='SST删除':deleted[e['文件']].append(int(e['经过纳秒']))
    end=max(int(z['查询结束纳秒']) for z in zones)
    extents=defaultdict(list);meta={}
    for e in mapping:extents[e['批次'],e['Zone']].append(e)
    for r in live:meta[r['批次'],r['文件']]=r
    histories=defaultdict(list);observations=[];file_observations=[]
    for z in zones:
        history=histories[z['Zone']]
        if z['阶段']=='1' and int(z['有效字节'])>0:
            now=int(z['查询结束纳秒']);parts=extents[z['批次'],z['Zone']]
            covered=sum(int(e['区内有效字节']) for e in parts)
            complete=z['统计稳定']=='1' and covered==int(z['映射覆盖字节'])==int(z['有效字节'])
            pre=dict(有效字节=int(z['有效字节']),映射与有效量一致=complete,当前最大块编号=int(z['块编号']),
                当前最大块RC=int(z['RC']),设备十秒增量=int(z['十秒增量']) if int(z['窗口标志'])&2 else None,
                设备三十秒增量=int(z['三十秒增量']) if int(z['窗口标志'])&4 else None,
                同块两秒=past_window(history,z,2,True),十秒有效量变化=past_window(history,z,10),
                三十秒有效量变化=past_window(history,z,30))
            deleted_bytes=0;labels_known=True;compacting=0;metadata_known=True
            byname=defaultdict(int)
            for e in parts:
                if e['文件'].endswith('.sst'):byname[e['文件']]+=int(e['区内有效字节'])
            for path,size in byname.items():
                m=meta.get((z['批次'],path));birth=created.get(path)
                label=delete_label(path,now,end,deleted);labels_known&=label['观察完整']
                if label['十秒内成功删除']:deleted_bytes+=size
                if not m:metadata_known=False
                elif m['正在Compaction']=='1':compacting+=size
                file_observations.append(dict(批次=z['批次'],Zone=z['Zone'],诊断文件=path,
                    事前=dict(可用截止纳秒=now,区内字节=size,映射与有效量一致=complete,
                        正在Compaction=m['正在Compaction']=='1' if m else None,
                        层级=int(m['层级']) if m else None,
                        已存活秒=(now-birth)/SECOND if birth is not None and birth<=now else None),
                    事后标签=label))
            pre.update(当前SST字节=sum(byname.values()),当前Compaction元数据齐全=metadata_known,
                当前正在Compaction字节=compacting if metadata_known else None)
            observations.append(dict(批次=z['批次'],Zone=z['Zone'],事前可见=pre,
                事后标签=dict(十秒删除观察完整=labels_known,十秒内成功删除的已映射SST字节=deleted_bytes if labels_known and complete else None,
                    说明='成功删除事件不等于文件句柄已经释放或整个Zone已经失效；不是未清理反事实')))
        history.append(z)
    groups={}
    for compact in (True,False,None):
        samples=[f for f in file_observations if f['事前']['映射与有效量一致'] and f['事前']['正在Compaction']==compact and f['事后标签']['观察完整']]
        groups[str(compact)]=dict(文件区快照样本数=len(samples),十秒内成功删除样本数=sum(bool(f['事后标签']['十秒内成功删除']) for f in samples))
    return dict(混合阶段非空区观察数=len(observations),当前Compaction状态与后续删除=groups,
        同块两秒历史可知数=sum(o['事前可见']['同块两秒']['可知'] for o in observations),
        十秒满区有效量历史可知数=sum(o['事前可见']['十秒有效量变化']['可知'] for o in observations),
        十秒满区有效量确有下降数=sum(o['事前可见']['十秒有效量变化'].get('净减少字节',0)>0 for o in observations),
        区快照与事前事后分栏=observations,文件快照与事前事后分栏=file_observations,
        边界='快照相关且重复，不是独立样本；未按未来标签挑选参数，未训练寿命预测器，未证明真实选区收益')

def analyze(run,expected,pressure=False,recent=False,paired=False,anchor=False,anchor_slow=False,execution_pair=None,migration_observe=False):
    require(sum((pressure,recent,paired,anchor,anchor_slow,execution_pair is not None,migration_observe))<=1,'实验配置不能混用')
    if migration_observe:anchor_slow=True
    if execution_pair is not None:
        require(execution_pair in ('greedy','guarded'),'未登记的执行配对模式')
        anchor_slow=True
    family='rocksdb-pressure-20261016' if pressure else LABEL
    if recent:family='rocksdb-recent-20261016'
    if paired:family='rocksdb-pair-rate-20261016'
    if anchor:family='rocksdb-anchor-20261016'
    if anchor_slow:family='rocksdb-anchor-slow-20261016';anchor=True
    keys=655360 if pressure or recent or paired or anchor else 524288
    write_keys=737280 if recent or paired or anchor else 983040
    require(run.name==family,'不是本轮真实RocksDB归档')
    for n in ('mkfs.exit','run.exit','verify.exit','runner.exit'):require((run/n).read_text().strip()=='0',n+'失败')
    for line in (run/'logs.sha256').read_text().splitlines():
        digest,name=line.split(maxsplit=1);relative=Path(name).relative_to('/home/femu/rrzc-stream-results/'+family)
        require(hashlib.sha256((run/relative).read_bytes()).hexdigest()==digest,'原始文件哈希不符')
    require((run/'identity.initial.sha256').read_bytes()==(run/'identity.final.sha256').read_bytes(),'运行期间软件改变')
    actual={n:d for d,n in (line.split() for line in (run/'identity.initial.sha256').read_text().splitlines())}
    require(all(actual.get(n)==d for n,d in expected.items()),'实际生产库／测试身份不符')
    config=dict(策略='Greedy影子观察',混合业务秒数='120',初始键数=str(keys),
        值字节='4096',覆盖写键数=str(write_keys),读次数='48000',RR阈值='4096',GC启动比例='20',额外预算字节='16777216')
    if recent or paired or anchor:config.update(覆盖写秒数='90',独立近期采样='1')
    if paired or anchor:config.update(双点速率影子='1')
    if anchor:config.update(条带数='4',每条带锚点键数='8192',可写键域大小='229376',锚点热读起始秒='45')
    if anchor_slow:config.update(混合业务秒数='180',覆盖写秒数='180',覆盖写速率键每秒='4096',读业务秒数='120')
    if execution_pair is not None:
        config.update(策略='Greedy影子观察' if execution_pair=='greedy' else 'RR-aware真实改选',双点速率影子='0',配对模式=execution_pair)
    if migration_observe:config.update(双点速率影子='0',迁移窗口观测='1')
    require(rows(run/'config.csv')==[config],'固定配置不符')
    events=rows(run/'events.csv');require(events and all(e['成功']=='1' for e in events),'数据库回调有错误')
    require([int(e['序号']) for e in events]==list(range(len(events))),'事件序号缺失')
    kinds=Counter(e['事件'] for e in events)
    require(kinds['SST创建']>0 and kinds['SST删除']>0 and kinds['Compaction完成']>0 and kinds['Flush完成']>0,'缺真实生命周期事件')
    reads,writes=rows(run/'reads.csv'),rows(run/'writes.csv')
    require(len(reads)==48000 and len(writes)==write_keys//64,'固定业务请求不完整')
    for i,r in enumerate(reads):
        key=524287 if i%2 else ((i//2)%16)*1024
        if anchor:
            key=((i//2)%16)*1024 if i<18000 or i%2==0 else ((i//2)%4)*65536+36864
            if i>=18000 and i%2:require(r['版本']=='0','保留锚点出现写入版本')
        require(int(r['序号'])==i and int(r['键号'])==key,'业务读取计划改变')
    for i,r in enumerate(writes):
        require(int(r['序号'])==i and int(r['起始键序号'])==i*64 and r['键数']=='64' and int(r['起始版本'])==i*64+1,'业务写入计划改变')
    if anchor_slow:
        # 只能验证请求计划；实际开始可能滞后，不能把计划速率当实际吞吐。
        origin=int(reads[0]['计划纳秒'])
        require(all(int(r['计划纳秒'])==origin+i*1000000000//400 for i,r in enumerate(reads)),'限速读取计划不符')
        require(all(int(r['计划纳秒'])==origin+i*64*1000000000//4096 for i,r in enumerate(writes)),'限速写入计划不符')
    verify=(run/'verify.stdout.log').read_text();require(f'最新值全量校验通过：键数={keys}，业务字节={keys*4096}' in verify,'最新值独立校验未确认')
    lines=(run/'zenfs.run.log').read_text().splitlines();text='\n'.join(lines)
    errors=[s for s in lines if '[ERROR]' in s or '状态=失败' in s]
    if migration_observe:
        require('迁移窗口统计：启用=1，' in text and '独立尾部约束：已启用，真实执行=0，' in text,'新观测入口或Greedy未生效')
        require('双点速率建议：' not in text and '双点速率配置：启用=1' not in text,'单次观测不启用双点影子')
        require((run/'migration.csv').is_file(),'缺本轮迁移标记')
    if execution_pair is not None:
        enabled=int(execution_pair=='guarded')
        require(f'独立尾部约束：已启用，真实执行={enabled}，' in text,'配对执行入口未生效')
        require('双点速率建议：' not in text and '双点速率配置：启用=1' not in text,'配对禁止混入双点影子')
    if '独立约束决策：' in text:decisions,_,maintenance=parse(lines,'guarded' if execution_pair=='guarded' else 'greedy',cost_rank=False)
    else:
        require(not any(m in text for m in ('简单双因素候选：','V1影子候选：','独立约束维护结果：')),'候选日志不完整')
        decisions=[];maintenance=[]
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    require(len(zc)==len(maintenance) and text.count('RR 开始：')==len(rr),'维护尝试缺统计')
    require(all(r[2] in (0,1) for r in rr) and text.count('RR 完成：')==sum(r[2] for r in rr),'RR成功与源重置不一致')
    if execution_pair!='guarded':require(not any(d['独立核验改选'] for d in decisions),'观察组实际改选')
    copied=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    require(all(c['复制成功']=='1' for c in copied),'复制失败')
    moved=sum(r[1] for r in zc+rr);require(sum(int(c['有效字节']) for c in copied)==moved,'全程迁移账目不符')
    require('RR 开始：' not in (run/'zenfs.verify.log').read_text() and 'GC 检查：' not in (run/'zenfs.verify.log').read_text(),'只读校验出现维护')
    signals=features(rows(run/'zones.csv'),rows(run/'files.csv'),rows(run/'live.csv'),events)
    starts=[int(r['计划纳秒']) for r in reads+writes]
    ends=[int(r['开始纳秒'])+int(r['延迟纳秒']) for r in reads+writes]
    return dict(记录与数据核验通过=True,维护无错误=not errors,正式性能基线可用=False,
        来源=str(run),软件身份=expected,生命周期事件=dict(kinds),
        读请求数=len(reads),覆盖写键数=len(writes)*64,ZC=len(zc),RR尝试=len(rr),RR成功=sum(r[2] for r in rr),
        RR未重置尝试=sum(r[2]==0 for r in rr),未重置RR实际迁移MiB=sum(r[1] for r in rr if r[2]==0)/MIB,
        维护错误原文=errors,总迁移MiB=moved/MIB,
        混合请求实际完成秒=(max(ends)-min(starts))/SECOND,
        实际ZC决策轮数=len(decisions),影子改选建议数=sum(d['原会改选']=='1' for d in decisions),
        重复迁移MiB=None,重复口径说明='真实SST可能有多个区片段，本轮未核算同一逻辑片段的重复搬运，不能将同名文件多次复制直接累加为重复量',
        平均与分位请求封装耗时=dict(读Get封装=latency([int(r['延迟纳秒']) for r in reads]),
            写64键批次封装=latency([int(r['延迟纳秒']) for r in writes])),
        计时边界='读封装包含键构造；写封装包含值和WriteBatch构造及DB::Write。不是纯API调用计时，不能与历史API延迟直接比较，也不是落盘或设备延迟。',
        可观察信号=signals,结论边界='真实RocksDB单次观察，不是策略性能对照或稳态；尚未增加生产模型因素')

def audit(root):
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'前后生产身份不同')
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    wrapper='run_rocksdb_observe_guest.sh'
    expected[f'/home/femu/rrzc-{LABEL}-tests/{wrapper}']=hashlib.sha256((root/'实验源'/wrapper).read_bytes()).hexdigest()
    return analyze(root/'运行归档'/LABEL,expected)

def factor_summary(result):
    """只按事先规定的过去十秒有无下降分组，不扫描阈值或据事后标签调模型。"""
    signal=result['可观察信号'];observations=signal['区快照与事前事后分栏'];groups={}
    for dropped in (True,False):
        usable=[o for o in observations if o['事前可见']['映射与有效量一致'] and
            o['事后标签']['十秒删除观察完整'] and o['事前可见']['十秒有效量变化']['可知'] and
            (o['事前可见']['十秒有效量变化']['净减少字节']>0)==dropped]
        groups['过去十秒下降' if dropped else '过去十秒未降']=dict(区快照样本数=len(usable),
            随后十秒至少一份已映射SST成功删除样本数=sum(o['事后标签']['十秒内成功删除的已映射SST字节']>0 for o in usable))
    files=signal['文件快照与事前事后分栏'];ages=[f['事前']['已存活秒'] for f in files if f['事前']['已存活秒'] is not None]
    return dict(过去有效量变化与后续删除=groups,
        当前Compaction状态与后续删除=signal['当前Compaction状态与后续删除'],
        只用过去同块两秒历史=dict(可知数=signal['同块两秒历史可知数'],
            各采样间隔均有读取数=sum(o['事前可见']['同块两秒'].get('各采样间隔均有读取',False) for o in observations)),
        文件年龄观测=dict(可知快照数=len(ages),最小秒=min(ages) if ages else None,最大秒=max(ages) if ages else None),
        结论='本次真实负载中存在可区分信号；快照相关且反复采到同一文件，不能称为独立预测准确率、因果收益或泛化能力。',
        接线边界='Compaction状态来自测试程序的RocksDB公开接口，当前生产ZenFS选择器没有接收该状态；不能说已经可直接用于生产改选。')

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要本轮观察归档根目录')
    root=Path(sys.argv[1]).resolve();require(root.name==LABEL,'不是本轮固定归档')
    output=root/'独立核验.json';require(not output.exists(),'结果已存在，拒绝覆盖')
    result=audit(root);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('真实RocksDB原始记录、最新值及事前／事后分栏核验通过。')
