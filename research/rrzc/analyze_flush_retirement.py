"""回溯Flush文件创建、RR复制和逻辑退役；未来信息只用于解释，不作为在线标签。"""
import argparse
from bisect import bisect_right
import csv
import json
from pathlib import Path
import re
from statistics import median
import tempfile
from analyze_all_blocks_dbbench import fields
from analyze_flush_control import context
from analyze_reproducibility import stamp
from analyze_sst_compaction import events
from audit_colocation_transactions import audit
from ycsb_af import digest, require

ZONE=128<<20


def within_horizon(copy,retired,end,seconds):
    """末端观察不足时返回未知，不能把删失样本当作永不退役。"""
    require(end>=copy and (retired is None or copy<=retired<=end), '退役/业务边界时间不合法')
    if retired is not None:return retired-copy<=seconds*10**9
    return False if end-copy>=seconds*10**9 else None


def next_generation_task(tasks,after,zone,epoch):
    return next((t for t in tasks if t['原始开始行']>after and t['Zone']==zone and t['源代际']==epoch),None)


def resolve_task(lines,task):
    """既有审计加过业务窗首行；重新定位并验证，导出真实原日志行号。"""
    starts=[n for n,line in enumerate(lines,1) if line==task['原始选择行']]
    require(len(starts)==1, '实际维护开始不唯一')
    offset=starts[0]-task['开始行']
    require(offset in (0,-1), '来源行号偏移不符合原审计窗口')
    result=dict(task,原始开始行=starts[0],原始结束行=task['结束行']+offset,审计包装行偏移=offset)
    for category,marker in (('复制','V1后续迁移复制：'),('提交','V1后续映射提交：')):
        result[category]=[]
        for item in task[category]:
            raw=item['行']+offset
            require(1<=raw<=len(lines) and marker in lines[raw-1]
                    and fields(lines[raw-1],marker)==item['字段'], '派生事务与真实原日志行不符')
            result[category].append(dict(item,原始行=raw,时刻=stamp(lines[raw-1])))
    require(1<=result['原始结束行']<=len(lines) and
            ('RR 统计：' if task['类型']=='RR' else 'ZC 统计：') in lines[result['原始结束行']-1],
            '实际事务汇总行不符')
    return result


def live_files(root,kind):
    with (root/f'布局-61.{kind}.csv').open() as stream:rows=list(csv.DictReader(stream))
    return {('/rrzc-dbbench'+r['文件'] if kind=='sst' else r['文件']) for r in rows}


def analyze_arm(root,proof,tags,mode,arm):
    log=root/'run.zenfs.log';lines=log.read_text().splitlines()
    # 从原日志重新计算全部事务，不能仅信任前轮派生JSON。
    with tempfile.TemporaryDirectory(prefix='rrzc-flush-retire-') as tmp:
        path=Path(tmp)
        (path/'zenfs.log').write_text('受控放置业务开始：派生窗口\n'+log.read_text()+'\n受控放置业务截止：派生窗口\n')
        rebuilt=audit(path)
    require(rebuilt['事务']==proof['事务'] and rebuilt['维护账目']==proof['维护'], '维护账目独立重算不同')
    tasks=[resolve_task(lines,t) for t in proof['事务']]
    retired={};resets={}
    for n,line in enumerate(lines,1):
        if 'V1后续文件退役：' in line:
            row=fields(line,'V1后续文件退役：');name=row['文件名']
            require(name not in retired,'同一文件重复退役')
            retired[name]=dict(行=n,时刻=stamp(line),文件编号=row['文件编号'])
        if 'Zone 重置诊断：' in line:
            row=fields(line,'Zone 重置诊断：')
            require(row['状态']=='成功', '存在失败Reset')
            resets.setdefault(int(row['Zone']),[]).append(n)
    end=max(stamp(l) for l in lines if re.match(r'^\d{4}/\d\d/\d\d-',l))
    records=events(root/'run.rocksdb.log')
    flush_started={r['记录']['job'] for r in records if r['记录'].get('event')=='flush_started'}
    flush_finished={r['记录']['job']:r for r in records if r['记录'].get('event')=='flush_finished'}
    require(flush_started==set(flush_finished), '业务Flush任务未闭合')
    created={};deleted={}
    for row in records:
        event=row['记录'];kind=event.get('event')
        if kind=='table_file_creation' and event.get('status')=='OK' and event['job'] in flush_started:
            name='/rrzc-dbbench/'+str(event['file_number']).zfill(6)+'.sst'
            require(name not in created, '同一Flush文件重复创建')
            created[name]=row
        if kind=='table_file_deletion' and event.get('status')=='OK':
            name='/rrzc-dbbench/'+str(event['file_number']).zfill(6)+'.sst'
            require(name not in deleted,'同一SST重复成功删除')
            deleted[name]=row
    require(len(created)==360, '真实Flush文件数不是已验收的360')
    final=live_files(root,'sst');extents=live_files(root,'extent')
    lifetimes=[]
    for name,row in created.items():
        event=row['记录'];dead=retired.get(name);remove=deleted.get(name)
        require(bool(dead)==bool(remove), 'ZenFS退役与RocksDB成功删除集合不一致')
        require((not dead and name in final and name in extents) or
                (dead and name not in final and name not in extents), '退役与末端SST/extent未闭合')
        birth=event['time_micros']*1000
        finished=flush_finished[event['job']]['记录']['time_micros']*1000
        require(birth<=finished<=end, 'Flush创建/完成越过业务时间边界')
        if dead:
            require(birth<=dead['时刻']<=remove['记录']['time_micros']*1000<=end, '退役与删除时间顺序不符')
        lifetimes.append(dict(模式=mode,臂=arm,文件=name,创建任务=event['job'],文件字节=event['file_size'],
            RocksDB创建行=row['行'],创建纳秒=birth,Flush完成行=flush_finished[event['job']]['行'],
            Flush完成纳秒=finished,ZenFS逻辑退役行=dead['行'] if dead else None,
            ZenFS逻辑退役纳秒=dead['时刻'] if dead else None,RocksDB删除行=remove['行'] if remove else None,
            创建至逻辑退役秒=(dead['时刻']-birth)/1e9 if dead else None,
            Flush完成至逻辑退役秒=(dead['时刻']-finished)/1e9 if dead else None,
            末端仍有效=not bool(dead),创建后至少观察秒=(end-birth)/1e9))
    rr_tasks=[t for t in tasks if t['类型']=='RR']
    require(len(rr_tasks)==7 and all(set(tags).issubset({c['字段']['文件名'] for c in t['复制']})
                                   for t in rr_tasks), '不是已验收的七次包含H的RR链')
    copies=[];summaries=[]
    for ordinal,task in enumerate(rr_tasks,1):
        w_rows=[]
        for item in task['复制']:
            q=item['字段'];name=q['文件名']
            if name not in created:continue
            require(q['复制成功']=='1', 'Flush文件复制失败')
            commits=[c for c in task['提交'] if c['原始行']>item['原始行'] and
                     c['字段']['文件编号']==q['文件编号'] and c['字段']['文件名']==name and c['字段']['成功']=='1']
            require(commits, '缺少真实成功映射提交')
            commit=commits[0];dead=retired.get(name);birth=created[name]['记录']['time_micros']*1000
            require(birth<=item['时刻']<=commit['时刻'], '创建/复制/映射时间不符')
            require(not dead or (dead['文件编号']==q['文件编号'] and dead['行']>commit['原始行']),
                    '已退役文件仍被复制或文件身份不符')
            target=int(q['目标Zone']);epoch=bisect_right(resets.get(target,[]),commit['原始行'])
            next_zone=next_generation_task(tasks,commit['原始行'],target,epoch)
            next_h=rr_tasks[ordinal] if ordinal<len(rr_tasks) else None
            subsequent=[(t,c) for t in tasks if t['原始开始行']>task['原始结束行'] for c in t['复制']
                        if c['字段']['文件编号']==q['文件编号'] and c['字段']['文件名']==name]
            next_copy=subsequent[0] if subsequent else None
            exact=bool(next_copy and next_copy[0]['源代际']==epoch and
                all(next_copy[1]['字段'][key]==q[value] for key,value in
                    (('源Zone','目标Zone'),('源片段','目标片段'),('有效字节','有效字节'))))
            row=dict(模式=mode,臂=arm,RR序号=ordinal,源ZoneID=task['Zone']//ZONE,
                源日志Reset序数=task['源代际'],文件=name,ZenFS文件编号=q['文件编号'],有效复制字节=int(q['有效字节']),
                目标ZoneID=target//ZONE,目标日志Reset序数=epoch,源片段=q['源片段'],目标片段=q['目标片段'],
                RR开始原始行=task['原始开始行'],复制原始行=item['原始行'],成功映射原始行=commit['原始行'],
                复制纳秒=item['时刻'],ZenFS逻辑退役原始行=dead['行'] if dead else None,
                复制后逻辑退役秒=(dead['时刻']-item['时刻'])/1e9 if dead else None,
                复制时文件年龄秒=(item['时刻']-birth)/1e9,
                复制早于Flush完成=item['时刻']<flush_finished[created[name]['记录']['job']]['记录']['time_micros']*1000,
                十秒内逻辑退役=within_horizon(item['时刻'],dead['时刻'] if dead else None,end,10),
                三十秒内逻辑退役=within_horizon(item['时刻'],dead['时刻'] if dead else None,end,30),
                下一次含H的RR原始行=next_h['原始开始行'] if next_h else None,
                退役早于下一次含H的RR=dead['行']<next_h['原始开始行'] if dead and next_h else None,
                目标区同代际下次维护原始行=next_zone['原始开始行'] if next_zone else None,
                退役早于目标区同代际下次维护=dead['行']<next_zone['原始开始行'] if dead and next_zone else None,
                后续再次维护复制此文件=bool(next_copy),后续同片段同代际链吻合=exact,
                后续再次复制原始行=next_copy[1]['原始行'] if next_copy else None,
                业务末端仍有效=not bool(dead))
            w_rows.append(row);copies.append(row)
        sums=lambda predicate:sum(r['有效复制字节'] for r in w_rows if predicate(r))
        h_bytes=sum(int(c['字段']['有效字节']) for c in task['复制'] if c['字段']['文件名'] in tags)
        w_bytes=sums(lambda _:True)
        summaries.append(dict(模式=mode,臂=arm,RR序号=ordinal,源ZoneID=task['Zone']//ZONE,
            原始开始行=task['原始开始行'],RR总复制字节=task['迁移字节'],H复制字节=h_bytes,
            Flush文件复制笔数=len(w_rows),Flush文件复制字节=w_bytes,其他复制字节=task['迁移字节']-h_bytes-w_bytes,
            十秒内已确认退役字节=sums(lambda r:r['十秒内逻辑退役'] is True),
            三十秒内已确认退役字节=sums(lambda r:r['三十秒内逻辑退役'] is True),
            下次含H的RR前已退役字节=sums(lambda r:r['退役早于下一次含H的RR'] is True),
            目标区同代际下次维护前已退役字节=sums(lambda r:r['退役早于目标区同代际下次维护'] is True),
            没有后续含H的RR可比较字节=sums(lambda r:r['下一次含H的RR原始行'] is None),
            末端仍有效Flush字节=sums(lambda r:r['业务末端仍有效'])))
    require(sum(s['RR总复制字节'] for s in summaries)==proof['维护']['RR迁移字节'], 'RR分解与维护账目不同')
    retired_lives=[r['创建至逻辑退役秒'] for r in lifetimes if not r['末端仍有效']]
    summary=dict(Flush创建文件数=len(created),业务窗内退役文件数=len(retired_lives),末端仍有效文件数=len(lifetimes)-len(retired_lives),
        已退役文件创建至退役中位秒=median(retired_lives),RR复制Flush笔数=len(copies),
        RR复制Flush去重文件数=len({r['文件'] for r in copies}),RR复制Flush字节=sum(r['有效复制字节'] for r in copies),
        RR复制后又退役的Flush字节=sum(r['有效复制字节'] for r in copies if not r['业务末端仍有效']),
        目标区同代际下次维护前退役字节=sum(r['有效复制字节'] for r in copies if r['退役早于目标区同代际下次维护'] is True),
        下次含H的RR前退役字节=sum(r['有效复制字节'] for r in copies if r['退役早于下一次含H的RR'] is True),
        同文件后续再次维护复制笔数=sum(r['后续再次维护复制此文件'] for r in copies),
        复制早于Flush完成笔数=sum(r['复制早于Flush完成'] for r in copies),
        十秒内已确认退役字节=sum(r['有效复制字节'] for r in copies if r['十秒内逻辑退役'] is True),
        三十秒内已确认退役字节=sum(r['有效复制字节'] for r in copies if r['三十秒内逻辑退役'] is True),
        十秒观察不足字节=sum(r['有效复制字节'] for r in copies if r['十秒内逻辑退役'] is None),
        审计包装行偏移=sorted({t['审计包装行偏移'] for t in tasks}),
        输入SHA256={n:digest(root/n) for n in ('run.zenfs.log','run.rocksdb.log','布局-61.sst.csv','布局-61.extent.csv')})
    return summary,lifetimes,copies,summaries


def analyze(comparison,output):
    require(not output.exists(), '不覆盖派生结果')
    paired=json.loads(comparison.read_text());reports={};life=[];copies=[];rr=[]
    for mode in ('off','on'):
        source=Path(paired['来源'][mode]);state,_,tags=context(source)
        require(state['镜像处理']['archive_sha256']==paired['完整归档SHA256'][mode], '配对来源归档改变')
        for arm in ('p1','p2'):
            proof=json.loads((source/(arm+'-核验.json')).read_text())
            report,a,b,c=analyze_arm(source/'逐臂采证'/arm,proof,tags,mode,arm)
            reports[mode+'/'+arm]=report;life+=a;copies+=b;rr+=c
    output.mkdir(parents=True)
    result=dict(来源配对=str(comparison),来源配对SHA256=digest(comparison),各臂=reports,
        分析源码SHA256=digest(Path(__file__)),
        边界='未来退役仅事后回溯；逻辑退役非精确used_capacity扣减时点，末端未退役为右删失；不主张推迟必要RR。')
    (output/'退役与RR时序核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for name,rows in (('Flush文件完整寿命.csv',life),('RR中Flush文件逐笔时序.csv',copies),('逐次RR时序汇总.csv',rr)):
        with (output/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('comparison',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();data=analyze(args.comparison,args.output)
    print(json.dumps(data['各臂'],ensure_ascii=False,indent=2))
