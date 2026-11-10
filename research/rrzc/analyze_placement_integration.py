"""第三阶段独立解析副本：核验真实空文件先退役、后登记的生命周期，不改策略与原解析器。"""
from bisect import bisect_right
from collections import Counter,defaultdict
import heapq
import json
from pathlib import Path
import struct

ROW=struct.Struct('<16Q')
KINDS={1:'区登记',2:'对象',3:'增加',4:'清空',5:'映射开始',6:'映射结束',7:'移除',
    8:'热度',9:'尝试',10:'G1跳过',11:'池目标',12:'实际目标',13:'复制',14:'事务结束',
    15:'Reset',16:'退役',17:'待提交引用',18:'不确定',19:'对象结束',20:'热度对象',
    21:'时钟',22:'模式',23:'验证开始',24:'验证片段',25:'验证结束',26:'验证跳过',27:'业务边界',
    28:'区角色',29:'元数据写入'}


def require(ok,message):
    if not ok:raise ValueError(message)


def rows(path):
    with path.open('rb') as f:
        require(f.read(8)==b'RRZCCP01','组成事件格式错误')
        raw=f.read(24);require(len(raw)==24,'组成事件头截断')
        count,dropped,complete=struct.unpack('<3Q',raw)
        require(complete==1 and dropped==0,'组成事件未完成写入或发生丢弃')
        require(path.stat().st_size==32+count*128,'组成事件长度不一致')
        for seq in range(1,count+1):
            row=ROW.unpack(f.read(128))
            require(row[0]==seq and row[3] in KINDS,'事件序号或类型不合法')
            yield row


def analyze(path):
    events=list(rows(Path(path)))
    owners={};zones={};profiles=set();heats=defaultdict(list);maps={};verifications={}
    early_retired={};empty_retired=[]
    pending=defaultdict(list);copies=defaultdict(list);closed=set();uncertain=[];transaction_owner={}
    history={};changes=[];targets=[];attempts=[];denials=[];boundaries=[];verified=0;skipped=0;counts=Counter();heat_checks=0
    zone_owners=defaultdict(set);profile_owners=defaultdict(set);expiry=[];heat_version={}
    for row in events:
        if row[3]==8:heats[row[5]].append(row)
    heat_seqs={profile:[r[0] for r in values] for profile,values in heats.items()}

    def heat(profile,event,clock):
        require(profile in profiles,'SST缺少热度对象登记')
        timeline=heats[profile];seqs=heat_seqs.get(profile,[])
        i=bisect_right(seqs,event)-1
        # Tick先于分类更新。其他线程已领取事件钟但未发布热度时，不假装原子快照。
        ambiguous=i+1<len(timeline) and timeline[i+1][6]<=clock
        value=timeline[i] if i>=0 else None
        hot=bool(value and value[7] and clock<value[8])
        ever=any(r[7] for r in timeline[:i+1])
        return hot,ever,not ambiguous

    def index_owner(owner):
        data=owners[owner]
        for key in data['totals']:zone_owners[key].discard(owner)
        data['totals']=Counter()
        for key,length in data['extents'].items():data['totals'][key[:3]]+=length
        for key in data['totals']:zone_owners[key].add(owner)

    def compose(seq,clock,selected=None):
        result={key:Counter() for key in (zones if selected is None else selected) if key in zones}
        for zkey,out in result.items():
            out['元数据区已写占用_非有效映射']=zones[zkey].get('元数据已写',0)
            for owner in zone_owners[zkey]:
                data=owners[owner];length=data['totals'][zkey];kind=data['kind']
                if data['temporary']:out['临时恢复引用']+=length
                elif data['retired']:out['退役保留引用']+=length
                elif kind==2:out['非SST字节']+=length
                elif kind==0:out['类别未确认字节']+=length
                else:
                    hot,ever,known=heat(data['profile'],seq,clock)
                    name=('H状态未确认' if not known else 'HW' if hot and data['w'] else
                        'H_only' if hot else 'W_only' if data['w'] else '未分类SST')
                    out[name]+=length
                    out['SST有效总量']+=length
                    if ever:out['历史曾H_SST字节']+=length
        for transaction,refs in pending.items():
            for device,zone,generation,length in refs:
                require((device,zone,generation) in zones,'待提交引用所属代际缺失')
                if (device,zone,generation) in result:result[(device,zone,generation)]['待提交迁移引用']+=length
        for value in result.values():
            for name in ('H_only','W_only','HW','未分类SST','H状态未确认','SST有效总量',
                         '非SST字节','类别未确认字节','临时恢复引用','退役保留引用','待提交迁移引用','历史曾H_SST字节'):
                value[name]+=0
            require(sum(value[k] for k in ('H_only','W_only','HW','未分类SST','H状态未确认'))==value['SST有效总量'],
                '互斥SST分类不闭合')
            value['已知H字节']=value['H_only']+value['HW']
            value['已知Flush字节']=value['W_only']+value['HW']
        return result

    global_clock=0
    for row in events:
        seq,ns,tid,kind,device,owner,clock=row[:7];a=row[7:];counts[KINDS[kind]]+=1
        global_clock=max(global_clock,clock);clock=global_clock
        affected=set(owners[owner]['totals']) if owner in owners else set()
        while expiry and expiry[0][0]<=clock:
            _,profile,version=heapq.heappop(expiry)
            if heat_version.get(profile)==version:
                for item in profile_owners[profile]:affected.update(owners[item]['totals'])
        if kind==8:
            heat_version[owner]=seq
            if a[0]:heapq.heappush(expiry,(a[1],owner,seq))
            for item in profile_owners[owner]:affected.update(owners[item]['totals'])
        if kind==1:
            key=(device,a[0],a[1]);require(key not in zones,'Zone代际重复初始化')
            zones[key]=dict(容量=a[2],可信=bool(a[3]))
            affected.add(key)
        elif kind==28:
            key=(device,a[0],a[1]);require(key in zones,'区角色缺少Zone登记')
            require(a[3]>=a[0],'Zone初始WP小于起点')
            zones[key]['元数据区']=bool(a[2])
            zones[key]['元数据已写']=a[3]-a[0] if a[2] else 0
            affected.add(key)
        elif kind==29:
            key=(device,a[0],a[1]);require(key in zones and zones[key].get('元数据区'),'元数据写入不在元数据区')
            require(a[2]>=a[0]+zones[key]['元数据已写'],'元数据WP倒退')
            zones[key]['元数据已写']=a[2]-a[0];affected.add(key)
        elif kind==20:profiles.add(owner)
        elif kind==21:
            hot,ever,known=heat(owner,seq,clock)
            require(known and hot==bool(a[0]),'热度投影与原Profile即时状态不一致')
            heat_checks+=1
        elif kind==2:
            require(a[2] in (0,1,2),'文件类别编码错误')
            if owner in early_retired:
                first,original_device=early_retired.pop(owner)
                require(a[5]==1 and a[6]==0 and original_device==device,'提前退役对象不是已确认的零长退役文件')
                empty_retired.append(dict(对象=owner,设备=device,退役事件=first,登记事件=seq,文件编号=a[0]))
            data=owners.setdefault(owner,dict(extents={},totals=Counter(),retired=False))
            if 'profile' in data:profile_owners[data['profile']].discard(owner)
            data.update(device=device,file=a[0],profile=a[1],kind=a[2],w=bool(a[3]),temporary=bool(a[4]),retired=bool(a[5]),size=a[6])
            profile_owners[a[1]].add(owner)
        elif kind==3:
            require(owner in owners,'extent增加缺少文件对象')
            key=(device,a[0],a[1],a[2]);require(a[3]>0 and a[4]==1,'extent长度为0或代际未知')
            container=maps[owner] if owner in maps else owners[owner]['extents']
            require(key not in container,'同一对象extent重复增加')
            container[key]=a[3]
            require(key[:3] in zones,'extent属于未登记或已结束的Zone代际')
            require(key[3]>=key[1] and key[3]+a[3]<=key[1]+zones[key[:3]]['容量'],'extent越过Zone容量')
            affected.add(key[:3])
            if owner not in maps:
                owners[owner]['totals'][key[:3]]+=a[3];zone_owners[key[:3]].add(owner)
        elif kind==4:
            require(owner in owners and owner not in maps,'清空缺少对象或处于映射事务内')
            owners[owner]['extents'].clear()
            index_owner(owner)
        elif kind==5:
            require(owner in owners and owner not in maps,'映射事务重复或缺少对象')
            maps[owner]=dict(owners[owner]['extents'])
        elif kind==7:
            key=(device,a[0],a[1],a[2]);require(owner in maps and maps[owner].get(key)==a[3],'源extent扣减不匹配')
            del maps[owner][key]
        elif kind==6:
            require(owner in maps,'映射结束缺少开始')
            owners[owner]['extents']=maps.pop(owner)
            index_owner(owner);affected.update(owners[owner]['totals'])
            for tx,item in transaction_owner.items():
                if item==owner:pending.pop(tx,None)
        elif kind==13:
            require(a[0] not in closed,'已完成事务继续复制')
            transaction_owner[a[0]]=owner
            copies[a[0]].append(dict(事件=seq,对象=owner,源Zone=a[1],源片段=a[2],目标Zone=a[3],目标片段=a[4],有效复制字节=a[5]))
        elif kind==17:
            require(a[0] not in closed,'已完成事务新增待提交引用')
            pending[a[0]].append((device,a[1],a[2],a[3]))
            transaction_owner[a[0]]=owner;affected.add((device,a[1],a[2]))
        elif kind==14:
            require(a[0] not in closed,'迁移事务重复完成')
            affected.update(ref[:3] for ref in pending.pop(a[0],[]));closed.add(a[0])
            for c in copies[a[0]]:c['映射结局']='提交' if a[1] else '未提交／撤销'
        elif kind==15:
            old=(device,a[0],a[1]);new=(device,a[0],a[2])
            require(old in zones and new not in zones and a[3]==1 and a[2]>a[1],'Reset代际不闭合')
            state=compose(seq,clock)[old]
            require(not any(state[k] for k in ('SST有效总量','临时恢复引用','退役保留引用','非SST字节','类别未确认字节','待提交迁移引用')),
                'Reset前仍有引用；禁止清零掩盖残留')
            zones[new]=dict(zones.pop(old));zones[new]['可信']=True
            zones[new]['元数据已写']=0
            history.pop(old,None)
            affected.add(new)
        elif kind==16:
            if owner in owners:owners[owner]['retired']=True
            else:
                require(owner not in early_retired,'未登记对象重复退役')
                early_retired[owner]=(seq,device)
        elif kind==18:uncertain.append(seq)
        elif kind==19:
            require(owner in owners and not owners[owner]['extents'] and owner not in maps,'对象销毁前引用未释放')
            profile_owners[owners[owner]['profile']].discard(owner)
            del owners[owner]
        elif kind==23:
            require(owner in owners and owner not in verifications,'快照核验缺少对象或重入')
            verifications[owner]={}
        elif kind==24:
            require(owner in verifications,'快照片段缺少开始')
            key=(device,a[0],a[1],a[2]);verifications[owner][key]=verifications[owner].get(key,0)+a[3]
        elif kind==25:
            require(owner in verifications and verifications[owner]==owners[owner]['extents'],'原生extent快照与事件重建不同')
            del verifications[owner];verified+=1
        elif kind==26:skipped+=1
        elif kind==9:
            attempts.append(dict(事件=seq,设备=device,对象=owner,文件编号=owners.get(owner,{}).get('file'),
                标签=a[0],H=bool(a[0]&1),Flush来源=bool(a[0]&2),新文件=bool(a[1]),需要容量=a[2],模式编码=a[3]))
        elif kind==10:
            require(a[0]&1,'G1拒绝记录不是H/HW')
            denials.append(dict(事件=seq,设备=device,对象=owner,文件编号=owners.get(owner,{}).get('file'),
                标签=a[0],原因='G1必要迁移H/HW跳过协同目标；不关闭H统计，不主动搬出原池数据'))
        if kind in (11,12):
            zkey=(device,a[0],a[1]);state=compose(seq,clock)
            require(zkey in state,'目标Zone代际缺失')
            value=state[zkey]
            tracked=sum(value[k] for k in ('SST有效总量','非SST字节','类别未确认字节',
                '退役保留引用','临时恢复引用','待提交迁移引用'))
            targets.append(dict(事件=seq,类型=KINDS[kind],Zone=a[0],代际=a[1],对象=owner,
                文件编号=owners.get(owner,{}).get('file'),协同目标=kind==11,标签=a[5],
                路径='新文件' if a[4] else '必要迁移',
                剩余可写=a[2],原生引用有效=a[3],重建引用=tracked,引用数值一致=tracked==a[3],
                当前组成=dict(value),H状态已确认=value['H状态未确认']==0,
                边界='非跨文件原子快照；数值不一致或H发布交错必须单列，不能驱动选择'))
        state=compose(seq,clock,None if kind==27 else affected)
        if kind==27:boundaries.append(dict(事件=seq,阶段=a[0],请求=a[1],各Zone=[dict(设备=k[0],Zone=k[1],代际=k[2],**v) for k,v in sorted(state.items())]))
        for key,value in state.items():
            prior=history.get(key,Counter())
            if value!=prior:
                changes.append(dict(事件=seq,单调纳秒=ns,事件钟=clock,原因=KINDS[kind],设备=key[0],Zone=key[1],代际=key[2],
                    增减={k:value[k]-prior[k] for k in value.keys()|prior.keys() if value[k]!=prior[k]},组成=dict(value)))
                history[key]=value
    require(not early_retired,'提前退役对象缺少后续登记证据')
    require(not maps and not verifications,'映射或原生快照事件未闭合')
    require(not pending and all(t in closed for t in copies),'迁移事务未闭合')
    require(not uncertain,'存在原生同步失败或Reset不确定事件，不能验证完整账目')
    return dict(空文件延后登记退役=empty_retired,事件账目闭合=True,原生映射核验次数=verified,忙文件快照跳过次数=skipped,热度即时对照次数=heat_checks,
        已有逐文件原生映射核验=verified>0,全盘原子映射验收=False,
        存在H发布交错=any(c['组成']['H状态未确认'] for c in changes),
        各类事件=dict(counts),协同尝试=attempts,G1拒绝=denials,目标=targets,组成变化=changes,业务边界=boundaries,
        复制明细=[c for rows in copies.values() for c in rows],
        有效复制字节=sum(c['有效复制字节'] for rows in copies.values() for c in rows),
        说明='四桶只计已确认的有效SST映射；非SST、未知类别、临时恢复、退役保留及待提交引用单列。'
            '元数据区按WP记录已写占用，不能等同于逻辑有效元数据或混入SST字节。'
            'H按原事件窗口可过期，历史曾H独立输出。事件钟不能换算精确的静默降温墙钟时刻。'
            '快照逐文件核验，忙文件明确跳过，不冒称跨层原子快照或完整NAND状态。')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trace',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();result=analyze(a.trace)
    with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
