"""读取真实生命周期布局、维护和请求；机会未出现也照实输出，不制造收益。"""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from analyze_all_blocks_dbbench import fields
from analyze_pool_dbbench import repeat_guard
from pool_dbbench_guest import table,key
from ycsb_af import digest,require
from analyze_reproducibility import request_rows

ZONE=128<<20


def persistent_mode(run):
    modes=re.findall(r'^SST生命周期计划：固定H=([01])$',run,re.M)
    require(len(modes)<=1,'生命周期计划标记重复')
    # 历史归档未提供该开关，默认是逐窗口读取；不改写原始日志。
    return modes==['1']


def planned_requests(persistent_h):
    """根据预先固定的循环生成输入，不使用任何维护结果。"""
    for window in range(6):
        for _ in range(2):
            for index in range(61440):yield 1,window*65536+4096+index
        for _ in range(8):
            for index in range(8192):yield 0,(0 if persistent_h else window*65536)+index%4096


def checked_requests(rows,persistent_h):
    expected=iter(planned_requests(persistent_h));counts=[0,0];number=0
    for row in rows:
        planned=next(expected,None)
        require(row[:4]==(number,planned[0],counts[planned[0]],planned[1]) if planned else False,
                '逻辑请求与预注册不同：序号='+str(number)+'，实际='+str(row[:4])+'，预期='+str(planned))
        counts[row[1]]+=1;number+=1;yield row
    require(next(expected,None) is None,'实际请求提前结束：'+str(number))


def timing(root):
    """使用新负载真实阶段及逐请求时间，拒绝借用旧48批假设。"""
    run=(root/'run.log').read_text()
    stages=[fields(line,'SST生命周期阶段：') for line in run.splitlines() if 'SST生命周期阶段：' in line]
    require([int(s['序号']) for s in stages]==list(range(1,61)),'60个真实阶段缺失或乱序')
    require([int(s['成功请求']) for s in stages][-1:]==[1130496],'末端成功请求数不符')
    expected=[];count=0
    for window in range(6):
        for part in range(2):
            count+=61440;expected.append(('W覆盖完成',window,part,count))
        for part in range(8):
            count+=8192;expected.append(('H读取完成',window,part,count))
    require([(s['阶段'],int(s['窗口']),int(s['段']),int(s['成功请求'])) for s in stages]==expected,
            '真实阶段类型、窗口或累计请求数与预注册不同')
    marks=[(int(i),int(n)) for i,n in re.findall(r'校准阶段边界：序号=(\d+)，纳秒=(\d+)',run)]
    require([i for i,_ in marks]==list(range(6)),'六个方法边界标记不完整')
    first=last=None;counts=[0,0]
    fixed_h=persistent_mode(run)
    for row in checked_requests(request_rows(root/'请求提交.bin'),fixed_h):
        if first is None:first=row[4]
        last=row[6];counts[row[1]]+=1
    require(counts==[393216,737280],'逐请求读写数量不符')
    times=[n for _,n in marks]
    start,end=[int((root/n).read_text()) for n in ('run.start.ns','run.end.ns')]
    require(start<=times[0]<=times[1]<=first<=last<=times[2]<=times[3]<=times[4]<=times[5]<=end,
            '生命周期方法与逐请求时间不闭合')
    require([int(s['纳秒']) for s in stages]==sorted(int(s['纳秒']) for s in stages),'阶段时间倒退')
    return dict(请求首尾秒=(last-first)/1e9,请求窗口吞吐ops=1130496/((last-first)/1e9),
        整阶段秒=(end-start)/1e9,整阶段吞吐ops=1130496/((end-start)/1e9),
        方法包含首尾维护秒=(times[2]-times[1])/1e9,
        固定H=fixed_h,逐条请求符合预注册=True,
        口径='逐请求首提交至末成功放行；包含中间Flush/Compaction/GC等待；末请求后检查另含于方法和整阶段，API耗时单列')


def arm(root,labels):
    run=(root/'run.log').read_text();log=(root/'run.zenfs.log').read_text()
    stages=[fields(line,'SST生命周期阶段：') for line in run.splitlines() if 'SST生命周期阶段：' in line]
    require([int(s['序号']) for s in stages]==list(range(1,61)),'60个真实阶段未完整记录')
    original=table(root/'布局-0.sst.csv');original_ids={r['文件'] for r in original}
    origin_h={r['文件'] for r in original if labels.get('/rrzc-dbbench'+r['文件'])=='H'}
    origin_w={r['文件'] for r in original if labels.get('/rrzc-dbbench'+r['文件'])=='W'}
    w_window={r['文件']:key(r['最小键十六进制'])//65536 for r in original if r['文件'] in origin_w}
    layouts=[];retired_before=set()
    for number in range(62):
        path=root/f'布局-{number}.sst.csv';extent=root/f'布局-{number}.extent.csv'
        require(path.is_file() and extent.is_file(),'缺少阶段实际SST/extent：'+str(number))
        rows=table(path);live={r['文件'] for r in rows}
        stage=stages[number-1] if 1<=number<=60 else None
        composed={};extents=table(extent)
        extent_files={e['文件'].removeprefix('/rrzc-dbbench') for e in extents}
        for e in extents:
            role=labels.get(e['文件'])
            if role in ('H','W'):
                z=int(e['Zone'])//ZONE;composed.setdefault(z,dict(H=0,W=0))[role]+=int(e['字节'])
        retired=origin_w-live;newly=retired-retired_before;retired_before=retired
        retired_windows={w:sum(w_window[name]==w for name in retired) for w in range(6)}
        layouts.append(dict(布局=number,阶段=stage,总SST=len(live),原SST存活=len(live&original_ids),
            原H存活=len(live&origin_h),原W存活=len(live&origin_w),原H已失效=sorted(origin_h-live),
            原W已失效=sorted(origin_w-live),原标签各Zone字节=composed,
            已失效W且快照无extent=sorted((origin_w-live)-extent_files),
            已失效W但快照仍有extent=sorted((origin_w-live)&extent_files),
            新失效W=sorted(newly),新失效W所属窗口=sorted({w_window[name] for name in newly}),
            原W失效按键窗口=retired_windows,
            混合Zone=[z for z,b in composed.items() if b['H'] and b['W']]))
    # 按本日志成功Reset隔开同一地址复用，避免把新代际RR归因于旧候选。
    epochs={};candidates=[];maintenance=[];last_observation={};last_qualification={};first_selection=None
    for number,line in enumerate(log.splitlines(),1):
        if 'Zone 重置诊断：' in line:
            row=fields(line,'Zone 重置诊断：')
            if row['状态']=='成功':
                z=int(row['Zone']);epochs[z]=epochs.get(z,0)+1
        if '候选范围资格：' in line:
            r=fields(line,'候选范围资格：');z=int(r['Zone'])
            last_qualification[(z,epochs.get(z,0))]={k:int(r[k]) for k in ('有效字节','容量字节','原比例门槛','原比例资格','实际比例资格')}
        if 'ZC 候选：Zone' in line:
            match=re.search(r'ZC 候选：Zone (\d+).*最大读数 (\d+).*选中 (\d+)',line)
            if match:
                z=int(match[1]);last_observation[(z,epochs.get(z,0))]=dict(行=number,时刻=line.split()[0],RC=int(match[2]),资格=last_qualification.get((z,epochs.get(z,0))))
        if '时序成本：' in line:
            if first_selection is None:first_selection=number
            r={k:int(v) for k,v in fields(line,'时序成本：').items()}
            if r['安全'] and 0<r['有效']<r['容量']:
                candidates.append(dict(**r,行=number,时刻=line.split()[0],本日志Reset序数=epochs.get(r['Zone'],0)))
        rr=re.search(r'RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节',line)
        zc=fields(line,'简单双因素实际集合：') if '简单双因素实际集合：' in line else None
        if rr or (zc and zc['实际选中']=='1'):
            z=int(rr[1] if rr else zc['Zone'])
            maintenance.append(dict(类型='RR' if rr else 'ZC',Zone=z,行=number,时刻=line.split()[0],
                本日志Reset序数=epochs.get(z,0),此前已进入GC选区=first_selection is not None,
                最近同代际GC观察=last_observation.get((z,epochs.get(z,0))) if rr else None))
    missed=[]
    for r in candidates:
        event=next((e for e in maintenance if e['行']>r['行'] and e['Zone']==r['Zone'] and e['本日志Reset序数']==r['本日志Reset序数']),None)
        if event and event['类型']=='RR':
            dt=lambda s:datetime.strptime(s,'%Y/%m/%d-%H:%M:%S.%f')
            missed.append(dict(候选=r,下一次维护=event,间隔秒=(dt(event['时刻'])-dt(r['时刻'])).total_seconds()))
    rr=[e for e in maintenance if e['类型']=='RR']
    return dict(固定H=persistent_mode(run),阶段布局=layouts,候选快照=len(candidates),正RR预计成本数=sum(r['RR复制']>0 for r in candidates),
        代际内候选随后RR快照=len(missed),涉及独立RR数=len({x['下一次维护']['行'] for x in missed}),
        候选随后RR详情=missed,真实RR=rr,
        首次GC选区前RR次数=sum(not e['此前已进入GC选区'] for e in rr),
        维护类型数=dict(RR=len(rr),ZC=len(maintenance)-len(rr)),
        边界='文件存活指本轮LiveFilesMetaData可见；逻辑H仍存活不代表原H文件或原Zone未移动。候选后续RR不等于可省复制，尚需反事实对照及末端账目。')


def analyze(output):
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None,'实验尚未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'完整归档哈希不同')
    source=output/'逐臂采证';proof=repeat_guard(source/'p1',source/'p2',request_count=1130496)
    require(proof['通过'],'新负载Greedy重复未通过')
    labels=json.loads((output/'源码/受控标签来源.json').read_text())['标签']
    reports={tag:arm(source/tag,labels) for tag in ('p1','p2')}
    expected_mode=json.loads((output/'预注册计划.json').read_text()).get('固定H',False)
    for tag,r in reports.items():
        require(r['固定H']==expected_mode,'实际读取模式与预注册不同')
        require(timing(source/tag)['逐条请求符合预注册'],'实际请求未逐条通过计划核验')
        ledger=json.loads((output/(tag+'-核验.json')).read_text())['维护']
        require(r['维护类型数']==dict(RR=ledger['RR次数'],ZC=ledger['ZC次数']),'维护回溯与真实账目不同')
    result=dict(通过=True,各臂=reports,重复=proof,完整归档SHA256=state['镜像处理']['archive_sha256'],
        无模型收益结论=True,目的='真实W文件失效、H映射变化与合格候选后续RR的机会筛查')
    target=output/'生命周期机会核验.json'
    with target.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    summary={tag:{k:v for k,v in report.items() if k not in ('阶段布局','真实RR','候选随后RR详情')} for tag,report in reports.items()}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path)
    analyze(parser.parse_args().results)
