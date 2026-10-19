"""单次真实Greedy迁移标记与候选历史复核；不训练、改选或计算假想节省量。"""
import hashlib,json
from collections import Counter,defaultdict
from pathlib import Path
from analyze_stream import rows
from analyze_rocksdb_observe import analyze,identity,SECOND
from analyze_rocksdb_guard_pair import request_identity
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_validity_signal import mapped_parts,future_deletions
from analyze_validity_examples import follow_empty
from analyze_rr_opportunities import stable,maintenance_audit
from analyze_v1_shadow_stream import fields,require
from validity_migration_window import index_markers,clean_trend,check_window

LABEL='rocksdb-migration-observe-20261017'
GUEST='rocksdb-anchor-slow-20261016'

def marker_audit(zones,raw,lines):
    markers=index_markers(raw)
    expected={(z['批次'],z['Zone'],side) for z in zones for side in ('前','后')}
    require(set(markers)==expected,'标记与Zone快照不一一对应')
    require(len({r['轨迹原点绝对纳秒'] for r in raw})==len({(r['实例'],r['创建绝对纳秒']) for r in raw})==1,'统计实例或轨迹原点改变')
    previous={};maximum=Counter();active=0;frames={}
    for z in zones:
        a,b=(markers[z['批次'],z['Zone'],side] for side in ('前','后'))
        origin=int(a['轨迹原点绝对纳秒'])
        checked=check_window(a,b,origin+int(z['查询开始纳秒']),origin+int(z['查询结束纳秒']))
        require(checked['可知'],'单次查询标记不可信：'+checked['原因'])
        for r in (a,b):
            require(r['启用']==r['可信']=='1' and int(r['序号'])>=int(r['活动数']) and (int(r['序号'])-int(r['活动数']))%2==0,'迁移活动／序号不一致')
            key=r['批次'],r['侧'];frame=tuple(r[k] for k in ('实例','创建绝对纳秒','冻结绝对纳秒','轨迹原点绝对纳秒'))
            require(key not in frames or frames[key]==frame,'同批次同侧冻结时刻不一致');frames[key]=frame
            maximum[r['Zone']]=max(maximum[r['Zone']],int(r['序号']));active+=int(r['活动数'])>0
        old=previous.get(z['Zone'])
        if old:require(int(a['序号'])>=int(old['序号']) and int(a['末次结束绝对纳秒'])>=int(old['末次结束绝对纳秒']),'跨快照迁移状态倒退')
        previous[z['Zone']]=b
    copies=[fields(line,'V1后续迁移复制：') for line in lines if 'V1后续迁移复制：' in line]
    touched={r[k] for r in copies for k in ('源Zone','目标Zone')}
    require(all(maximum[z]>0 for z in touched),'真实复制的源／目标未观测到标记')
    return dict(原始标记行=len(raw),查询批次=len(frames)//2,活动标记行=active,
        实际复制记录=len(copies),复制触及区数=len(touched),有标记区数=sum(v>0 for v in maximum.values()),
        末次仍活动区数=sum(int(r['活动数'])>0 for r in previous.values()),
        各Zone最大序号=dict(maximum),
        边界='源准备可保守标记尚未复制的片段；末次采样可能仍有在途维护，不把序号直接换算复制字节或精确操作次数。'),markers

def contexts(lines):
    hosts={};geometry={};blocks={};frames={};files=defaultdict(dict);summaries={};decisions={}
    for number,line in enumerate(lines,1):
        if 'GC 检查：' in line:hosts={};geometry={}
        if 'Zone 维护观测：' in line:
            r=fields(line,'Zone 维护观测：')
            if r['阶段']=='ZC候选':hosts[r['Zone']]=r
        if '简单双因素候选：' in line:
            r=fields(line,'简单双因素候选：');geometry[r['Zone']]=r
        if '迁移窗口候选：' in line:
            r=fields(line,'迁移窗口候选：');key=r['轮次'],r['Zone'],r['侧']
            require(key not in frames,'重复候选迁移标记');frames[key]=r
        if 'V1影子候选：' in line:
            r=fields(line,'V1影子候选：');key=r['轮次'],r['Zone']
            require(key not in blocks and r['Zone'] in hosts and r['Zone'] in geometry,'候选身份或空间上下文缺失')
            blocks[key]=dict(块=r,主机=hosts[r['Zone']],空间=geometry[r['Zone']],行号=number)
        if 'V1影子文件：' in line:
            r=fields(line,'V1影子文件：');key=r['轮次'],r['Zone'];fid=r['文件编号'],r['文件名']
            require(fid not in files[key],'重复候选文件');files[key][fid]=int(r['区内有效字节'])
        if '独立约束候选：' in line:
            r=fields(line,'独立约束候选：');summaries[r['轮次'],r['Zone']]=r
        if '独立约束决策：' in line:
            r=fields(line,'独立约束决策：');decisions[r['轮次']]=r
    require(set(frames)=={(*key,side) for key in blocks for side in ('前','后')},'真实候选迁移标记不完整')
    return blocks,frames,files,summaries,decisions

def bridge(snapshot,candidate,host,before,after,markers,parts,files):
    """只连接决策前最近已完成的快照；不冒充GC决策时的原子映射。"""
    age=int(before['冻结绝对纳秒'])-int(before['轨迹原点绝对纳秒'])-int(snapshot['查询结束纳秒'])
    if not 0<age<=2500000000 or int(after['冻结绝对纳秒'])<int(before['冻结绝对纳秒']):
        return dict(可用=False,原因='快照未严格早于候选、过旧或当前标记时间倒退')
    complete,_=mapped_parts(snapshot,parts)
    if not stable(snapshot) or not complete or candidate.get('统计准入')!='1':
        return dict(可用=False,原因='最近快照或当前候选统计／映射未知')
    generations=host['主机代际'].split('/');sequences=host['观察序号'].split('/')
    if host['重置窗稳定']!='1' or len(set(generations))!=1 or len(set(sequences))!=1 or generations[0]!=snapshot['主机代际后'] or sequences[0]!=snapshot['主机序号后']:
        return dict(可用=False,原因='候选与过去快照的主机Reset身份未确认相同')
    if candidate['设备会话']!=snapshot['会话高位']+'/'+snapshot['会话低位'] or candidate['设备代际']!=snapshot['设备代际后']:
        return dict(可用=False,原因='候选与过去快照的设备会话或Reset身份改变')
    aggregate=defaultdict(int)
    for p in parts[snapshot['批次'],snapshot['Zone']]:aggregate[p['文件编号'],p['文件']]+=int(p['区内有效字节'])
    if candidate['有效字节']!=snapshot['有效字节'] or host['快照写指针']!=snapshot['写指针'] or dict(aggregate)!=files:
        return dict(可用=False,原因='候选有效量、写指针或当前SST集合与最近快照不同')
    start=markers.get((snapshot['批次'],snapshot['Zone'],'前'))
    result=check_window(start,after,int(start['轨迹原点绝对纳秒'])+int(snapshot['查询开始纳秒']),int(after['冻结绝对纳秒'])) if start else dict(可知=False,原因='缺快照前迁移标记')
    return dict(可用=result['可知'] and result.get('无迁移触及',False),历史迁移=result,
        边界='趋势截至最近已完成快照，延迟单列；当前一致性核对与无迁移桥接不等于原子快照。')

def subsequent_empty(current,zones,candidate_marker,markers):
    """事后清空标签另查前后标记，不能把本轮Greedy清理误认自然失效。"""
    empty=follow_empty(current,zones)
    if not empty['确认空区']:return dict(确认无迁移清空=False,清空观察=empty)
    z=empty['空区快照'];r=markers.get((z['批次'],z['Zone'],'后'))
    clean=(r is not None and r['启用']==r['可信']=='1' and
        r['实例']==candidate_marker['实例'] and r['序号']==candidate_marker['序号'] and r['活动数']=='0' and candidate_marker['活动数']=='0')
    return dict(确认无迁移清空=clean,清空观察=empty,
        边界='未来事实仅作标签；无迁移清空也不是改选反事实收益，观察截断／未清空不等于不会失效。')

def inspect(zones,mapping,events,raw,lines):
    marker_result,markers=marker_audit(zones,raw,lines)
    parts=defaultdict(list);deleted=defaultdict(list);byzone=defaultdict(list)
    for p in mapping:parts[p['批次'],p['Zone']].append(p)
    for z in zones:byzone[z['Zone']].append(z)
    for e in events:
        if e['事件']=='SST删除' and e['成功']=='1':deleted[e['文件']].append(int(e['经过纳秒']))
    origin=int(raw[0]['轨迹原点绝对纳秒']);end=max(int(z['查询结束纳秒']) for z in zones)
    blocks,frames,files,summaries,decisions=contexts(lines);maintenance=maintenance_audit(lines)
    observations=[];first=set();counts=Counter()
    for key,context in blocks.items():
        rid,zone=key;b=dict(context['块']);a=frames[rid,zone,'前'];c=frames[rid,zone,'后']
        # 当前块查询缺失时，原资格仍取独立标记，不把统计缺失误作无风险。
        b.setdefault('原候选资格',c['原候选资格'])
        for f in (a,c):f['轨迹原点绝对纳秒']=str(origin)
        before_ns=int(a['冻结绝对纳秒'])-origin;after_ns=int(c['冻结绝对纳秒'])-origin
        past=[z for z in byzone[zone] if int(z['查询结束纳秒'])<before_ns]
        snapshot=past[-1] if past else None
        age=before_ns-int(snapshot['查询结束纳秒']) if snapshot else None
        joined=bridge(snapshot,b,context['主机'],a,c,markers,parts,files[key]) if snapshot and 0<=age<=2500000000 else dict(可用=False,原因='缺决策前2.5秒内已完成快照')
        summary=summaries[key];urgent=summary['短期预测可知']=='1' and int(summary['短期达阈值纳秒'])<=10*SECOND
        risk='当前已达阈值' if int(b.get('当前RC','0'))>=4096 else '阈值前近期风险' if urgent else '未确认近期紧迫'
        for window in (10,30):
            feature=clean_trend(past[:-1],snapshot,window,parts,markers) if joined['可用'] else dict(可用=False,原因=joined.get('原因',joined.get('历史迁移',{}).get('原因')))
            record=dict(轮次=rid,Zone=zone,窗口秒=window,原候选资格=b['原候选资格'],当前风险分类=risk,
                是当轮Greedy=decisions[rid]['GreedyZone']==zone,当前RC=b.get('当前RC'),当前有效字节=b['有效字节'],
                事前证据=dict(最近快照批次=snapshot['批次'] if snapshot else None,快照龄秒=age/SECOND if age is not None else None,一致性桥接=joined,趋势=feature))
            counts[f'{window}秒_所有候选']+=1
            if b['原候选资格']=='1':
                counts[f'{window}秒_原合格候选']+=1
                if feature['可用']:counts[f'{window}秒_合格且干净趋势可用']+=1
                if urgent:
                    counts[f'{window}秒_合格且近期紧迫']+=1
                    if feature['可用']:counts[f'{window}秒_紧迫且干净趋势可用']+=1
            # 当前文件和有效量一致才生成当前内容标签；匹配失败不移用旧快照。
            if joined['可用']:
                current=dict(snapshot,查询开始纳秒=str(before_ns),查询结束纳秒=str(after_ns),RC=b['当前RC'])
                record['事后标签']=dict(当前SST=future_deletions(current,parts,deleted,end),
                    十秒清空=subsequent_empty(current,byzone[zone],c,markers),
                    后续同主机代际RR=[r['后续RR'] for r in maintenance['同地址后续RR核对'] if str(r['候选']['轮次'])==rid and r['候选']['Zone']==zone and r['同一主机代际']],
                    边界='后续RR为观察剩余全程事实，不承诺十秒内；没有时间证据不补RR起点或反事实收益。')
            if b['原候选资格']=='1' and feature['可用']:
                cohort=(window,identity(snapshot))
                record['每区代际首个可用观察']=cohort not in first;first.add(cohort)
            observations.append(record)
    groups={}
    for window in (10,30):
        groups[str(window)]={}
        for risk in ('全部原合格候选','近期紧迫原合格候选'):
            selected=[r for r in observations if r['窗口秒']==window and r.get('每区代际首个可用观察') and (risk=='全部原合格候选' or r['当前风险分类']!='未确认近期紧迫')]
            groups[str(window)][risk]={}
            for state in ('未下降','单次下降','多次下降'):
                group=[r for r in selected if r['事前证据']['趋势']['事前趋势']['分类']==state]
                groups[str(window)][risk][state]=dict(首观察数=len(group),
                    十秒删除观察完整数=sum(r['事后标签']['当前SST']['完整十秒删除观察'] for r in group),
                    十秒有当前SST删除数=sum(r['事后标签']['当前SST']['十秒内成功删除SST字节'] is not None and r['事后标签']['当前SST']['十秒内成功删除SST字节']>0 for r in group),
                    十秒确认无迁移清空数=sum(r['事后标签']['十秒清空']['确认无迁移清空'] for r in group),
                    观察剩余全程同代RR数=sum(bool(r['事后标签']['后续同主机代际RR']) for r in group))
    return dict(迁移标记=marker_result,真实GC候选覆盖=dict(counts),每区代际首观察分组=groups,候选明细=observations,
        边界='当前真实候选资格与当时最近已完成快照分开；不按未来筛样。窗口／轮次相关，当前实际Greedy维护会干预结局，不能将标签或RR次数当作新机制收益。')

def audit(root):
    require(root.name==LABEL,'不是本轮新观测目录')
    expected=json.loads((root/'本轮软件身份.json').read_text());run=root/'运行归档'/GUEST
    require((root/'运行身份.before.sha256').read_bytes()==(root/'运行身份.after.sha256').read_bytes(),'运行身份改变')
    base=analyze(run,expected,migration_observe=True)
    require(base==json.loads((root/'基础核验.json').read_text()) and base['维护无错误'],'基础完整核验复算不符')
    result=inspect(rows(run/'zones.csv'),rows(run/'files.csv'),rows(run/'events.csv'),rows(run/'migration.csv'),(run/'zenfs.run.log').read_text().splitlines())
    return dict(基础核验复算一致=True,全程维护={k:base[k] for k in ('ZC','RR成功','总迁移MiB','读请求数','覆盖写键数')},
        信号核验=result,输入SHA256={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('zones.csv','files.csv','events.csv','migration.csv','zenfs.run.log')},
        分析源SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        结论边界='只验证过去迁移污染排除与真实候选覆盖；不拟合寿命、不证明新模型减少RR或搬运，不把单次180秒称作稳态。')

def diagnostic_audit(root):
    """严格验收失败时只出异常诊断，不降低原验收要求或生成正式成功报告。"""
    require(root.name==LABEL,'不是本轮新观测目录')
    run=root/'运行归档'/GUEST;expected=json.loads((root/'本轮软件身份.json').read_text())
    require((root/'运行身份.before.sha256').read_bytes()==(root/'运行身份.after.sha256').read_bytes(),'运行软件身份改变')
    require((run/'identity.initial.sha256').read_bytes()==(run/'identity.final.sha256').read_bytes(),'客机运行身份改变')
    actual={n:d for d,n in (s.split() for s in (run/'identity.initial.sha256').read_text().splitlines())}
    require(all(actual.get(n)==d for n,d in expected.items()),'归档软件与实际构建不符')
    for line in (run/'logs.sha256').read_text().splitlines():
        digest,name=line.split(maxsplit=1);relative=Path(name).relative_to('/home/femu/rrzc-stream-results/'+GUEST)
        require('..' not in relative.parts and hashlib.sha256((run/relative).read_bytes()).hexdigest()==digest,'原始文件哈希不符')
    for n in ('mkfs.exit','run.exit','verify.exit','runner.exit'):
        require((run/n).read_text().strip()=='0','业务或最新值校验未完成，不能使用完整诊断入口')
    require('最新值全量校验通过：键数=655360，业务字节=2684354560' in (run/'verify.stdout.log').read_text(),'最新值校验未确认')
    reads,writes=rows(run/'reads.csv'),rows(run/'writes.csv')
    require(len(reads)==48000 and len(writes)==11520,'固定业务请求不完整')
    previous=root.parent/'rocksdb-guard-pair-20261016/greedy/运行归档'/GUEST
    require(request_identity(reads,writes)==request_identity(rows(previous/'reads.csv'),rows(previous/'writes.csv')),'原固定请求计划不符')
    events=rows(run/'events.csv');require(events and all(e['成功']=='1' for e in events),'数据库生命周期有失败事件')
    try:analyze(run,expected,migration_observe=True)
    except ValueError as error:failure=str(error)
    else:raise ValueError('正式验收未失败，不应使用异常诊断入口')
    lines=(run/'zenfs.run.log').read_text().splitlines()
    errors=[dict(行号=i,原文=s) for i,s in enumerate(lines,1) if '[ERROR]' in s]
    maintenance=[fields(s,'独立约束维护结果：') for s in lines if '独立约束维护结果：' in s]
    failed=[r for r in maintenance if r['迁移成功']!='1' or r['本轮重置区数']!='1']
    require(errors and failed,'没有核实到维护错误，不能将未知验收错误归因为维护失败')
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    copied=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr)
    require(all(r['复制成功']=='1' for r in copied) and sum(int(r['有效字节']) for r in copied)==moved,'异常中的实际复制字节账目不符')
    result=inspect(rows(run/'zones.csv'),rows(run/'files.csv'),events,rows(run/'migration.csv'),lines)
    return dict(正式验收通过=False,正式信号验证可用=False,异常诊断可复算=True,
        完整业务与最新值校验通过=True,业务与校验退出码均零=True,原固定请求计划相同=True,
        严格验收原始错误=failure,维护错误原文=errors,未完整维护=failed,
        实际维护账目=dict(ZC尝试=len(zc),ZC源重置成功=sum(r[2] for r in zc),RR尝试=len(rr),RR源重置成功=sum(r[2] for r in rr),
            已复制总字节=moved,已复制总MiB=moved/(1<<20),失败ZC已复制字节=sum(int(r['本轮迁移字节']) for r in failed)),
        异常归档信号诊断=result,
        输入SHA256={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('zones.csv','files.csv','events.csv','migration.csv','zenfs.run.log','logs.sha256')},
        分析源SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        结论边界='异常轮次及后续快照只作诊断；正式验收仍失败，不与正常基线比较收益，不据此接入趋势门槛、自动重跑或修改迁移逻辑。')

if __name__=='__main__':
    import sys
    require(len(sys.argv) in (2,3) and (len(sys.argv)==2 or sys.argv[2]=='--diagnostic'),'需要新观测根目录，可显式选择--diagnostic')
    root=Path(sys.argv[1]).resolve();diagnostic=len(sys.argv)==3
    output=root/('异常诊断.json' if diagnostic else '迁移窗口信号核验.json')
    require(not output.exists(),'报告已存在，拒绝覆盖')
    result=diagnostic_audit(root) if diagnostic else audit(root)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('异常诊断已保存，正式验收仍失败，不作为正常收益或预测验证。' if diagnostic else '新迁移标记与真实GC候选历史完成独立复核；未执行新改选或计算假想收益。')
