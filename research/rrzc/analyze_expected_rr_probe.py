"""独立只读核验同块预测与后续轨迹；删失不计为未到阈值，不推导改选收益。"""
import calendar
from collections import Counter,defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from analyze_joint_opportunity import fields,require
from analyze_persistent_credit import analyze_arm
from analyze_recent_eta import validate_rate
from replay_expected_rr import ROOT,SECOND,MAX,SCALE,inputs_from_log,calculate


def stamp(line):
    value=datetime.strptime(line.split()[0],'%Y/%m/%d-%H:%M:%S.%f')
    return calendar.timegm(value.timetuple())*SECOND+value.microsecond*1000


def clock_offset(lines):
    anchors=[(stamp(s),int(fields(s,'块轨迹对时：')['墙钟纳秒'])) for s in lines if '块轨迹对时：' in s]
    require(len(anchors)==1,'需要唯一真实时钟锚点，不能猜测客机日志时区')
    naive,epoch=anchors[0];unit=900*SECOND
    offset=((naive-epoch+unit//2)//unit)*unit
    require(abs(naive-epoch-offset)<SECOND,'日志与轨迹墙钟不能可靠对齐')
    return offset


IDENTITY=('设备会话高位','设备会话低位','Zone','设备代际','块编号','通道','LUN','plane','block','代际起点纳秒')


def read_trace(path,expected_zones=32):
    by_zone=defaultdict(list);frames={};last={}
    with Path(path).open() as stream:
        for raw in csv.DictReader(stream):
            require(None not in raw and all(v is not None for v in raw.values()),'块轨迹存在截断行')
            row={k:int(v) for k,v in raw.items()};batch=row['批次'];z=row['Zone']
            require(all(v>=0 for v in row.values()),'块轨迹出现负字段')
            require(row['查询开始纳秒']<=row['查询结束纳秒'] and
                row['查询结束纳秒']-row['查询开始纳秒']<=2*SECOND,'轨迹查询过慢或倒退')
            require(row['区数']==expected_zones and row['RR阈值']==1024 and
                row['设备会话高位']+row['设备会话低位']>0,'轨迹几何、阈值或会话不符')
            require(row['代际起点纳秒']<=row['设备快照纳秒'] and
                row['十秒真实增量']<=row['三十秒真实增量']<=row['RC'] and
                row['窗口标志']<=15,'轨迹计数或代际时间无效')
            frame=frames.setdefault(batch,{})
            require(z not in frame,'同批次Zone重复');frame[z]=row
            if z in last:require(row['设备快照纳秒']>last[z],'设备采样时间没有递增')
            last[z]=row['设备快照纳秒'];by_zone[z].append(row)
    require(frames and sorted(frames)==list(range(len(frames))),'采样批次为空或不连续')
    common=None;previous=None
    for frame in frames.values():
        require(len(frame)==expected_zones,'采样帧不完整')
        require(common is None or set(frame)==common,'轨迹Zone集合变化');common=set(frame)
        one=next(iter(frame.values()))
        same=('批次','查询开始纳秒','查询结束纳秒','查询墙钟纳秒','设备会话高位','设备会话低位','RR阈值','区数')
        require(all(all(row[k]==one[k] for k in same) for row in frame.values()),'同帧查询元数据不一致')
        if previous:
            mono=one['查询结束纳秒']-previous['查询结束纳秒']
            wall=one['查询墙钟纳秒']-previous['查询墙钟纳秒']
            require(mono>0 and abs(wall-mono)<SECOND//10,'采样墙钟跳变，不能对齐维护事件')
        previous=one
    return by_zone,len(frames)


def point_from_block(b):
    hi,lo=map(int,b['设备会话'].split('/'));ch,lun,plane,block=map(int,b['PPA'].split('/'))
    return dict(设备会话高位=hi,设备会话低位=lo,Zone=int(b['Zone']),设备代际=int(b['设备代际']),
        块编号=int(b['块编号']),通道=ch,LUN=lun,plane=plane,block=block,
        代际起点纳秒=int(b['代际起点纳秒']),设备快照纳秒=int(b['快照纳秒']),RC=int(b['当前RC']))


def outcome(point,observations,threshold,horizon,barriers=()):
    """右删失和跨窗口区间保持未知；不同最大块不能在稍后换回来时重新拼接。"""
    previous=point;t0=point['设备快照纳秒']
    if point['RC']>=threshold:return dict(状态='原本已达阈值',窗口内到阈值=None)
    barrier=min(barriers,key=lambda e:e['墙钟纳秒']) if barriers else None
    for row in observations:
        now=row['设备快照纳秒']
        if now<=t0:continue
        if barrier and row['查询墙钟纳秒']>=barrier['墙钟纳秒']:
            return dict(状态='删失',窗口内到阈值=None,原因=barrier['原因'])
        if any(row[k]!=point[k] for k in IDENTITY):
            return dict(状态='删失',窗口内到阈值=None,原因='会话、Reset代际或最大块身份改变')
        if not row['窗口标志']&1 or row['窗口标志']&8:
            return dict(状态='删失',窗口内到阈值=None,原因='统计关闭或有丢失')
        if now-previous['设备快照纳秒']>1500000000 or row['RC']<previous['RC']:
            return dict(状态='删失',窗口内到阈值=None,原因='采样空档超过1.5秒或同块RC倒退')
        low=previous['设备快照纳秒']-t0;high=now-t0
        if row['RC']>=threshold:
            label=True if high<=horizon else False if low>=horizon else None
            return dict(状态='观察到跨越' if label is not None else '跨越区间横跨窗口边界',
                窗口内到阈值=label,下界纳秒=low,上界纳秒=high,上界RC=row['RC'])
        if high>=horizon:
            return dict(状态='完整窗口内未到阈值',窗口内到阈值=False,确认至纳秒=high,确认RC=row['RC'])
        previous=row
    return dict(状态='删失',窗口内到阈值=None,原因='观察结束前未完整覆盖预测窗口')


def events(lines,offset):
    result=[]
    for i,line in enumerate(lines):
        entry=None
        if '统一决策：' in line:
            d=fields(line,'统一决策：')
            if int(d['实际Zone']):entry=dict(Zone=int(d['实际Zone']),原因='实际ZC选中，停止自然读轨迹核验')
        elif match:=re.search(r'RR 开始：Zone (\d+)，',line):
            entry=dict(Zone=int(match[1]),原因='独立RR开始，后续维护读取不能用来验证原自然速率')
        elif 'V1后续文件退役：' in line:
            match=re.search(r'文件编号=(\d+)，',line)
            require(match is not None,'文件退役缺少真实编号')
            entry=dict(文件编号=match[1],原因='有效文件退役，停止假设持续存活的阈值核验')
        if entry:result.append(dict(entry,行号=i,墙钟纳秒=stamp(line)-offset))
    return result


def analyze(root,scenario):
    root=Path(root);actual=analyze_arm(root,'greedy',scenario)
    require(not any('期望RR配置：' in s for s in (root/'zenfs.run.log').read_text().splitlines()),'旁观测量不能启用新模型')
    status=dict(s.split('=',1) for s in (root/'block-trace.status').read_text().splitlines())
    require(status['完整']=='1' and not status['原因'],'旁观轨迹未正常完成')
    observations,count=read_trace(root/'block-trace.csv')
    require(int(status['批次'])==count,'轨迹批次和收尾状态不一致')
    lines=(root/'zenfs.run.log').read_text().splitlines();offset=clock_offset(lines)
    points={};rates={};files=defaultdict(set)
    for i,line in enumerate(lines):
        for tag,index in (('V1影子候选',points),('近期ETA对照',rates)):
            if tag+'：' in line:
                b=fields(line.split('，原因=',1)[0],tag+'：');index[int(b['轮次']),int(b['Zone'])]=(i,b)
        if 'V1影子文件：' in line:
            f=fields(line,'V1影子文件：');files[int(f['轮次']),int(f['Zone'])].add(f['文件编号'])
    barriers=events(lines,offset);reports=[]
    with tempfile.TemporaryDirectory(prefix='rrzc-probability-probe-') as temp:
        binary=Path(temp)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_expected_rr_selector.cc'),'-o',str(binary)],check=True,capture_output=True)
        for decision,inputs in inputs_from_log(root/'zenfs.run.log','greedy'):
            n=int(decision['决策']['轮次']);costs=calculate(binary,[r for r,_ in inputs])
            for (row,safe),cost in zip(inputs,costs):
                key=n,row[0];line,b=points[key]
                if not safe or b.get('统计准入')!='1':continue
                _,rate=rates[key];validate_rate(rate,b);point=point_from_block(b)
                matching=[e for e in barriers if e['行号']>line and
                    (e.get('Zone')==row[0] or e.get('文件编号') in files[key])]
                result=outcome(point,observations[row[0]],row[8],cost[6] or 10*SECOND,matching)
                selected=row[0]==int(decision['决策']['实际Zone'])
                if selected:result=dict(状态='本轮ZC已干预',窗口内到阈值=None)
                reports.append(dict(轮次=n,Zone=row[0],块编号=point['块编号'],代际=point['设备代际'],
                    当前RC=row[3],近期增量=row[19],近期可知=bool(row[18]),预计ETA纳秒=cost[1],
                    支撑窗口纳秒=cost[6],概率可计算=bool(cost[7]),需求概率十亿分数=cost[8],
                    预计RR抵扣=cost[3],预计留存=cost[2],本轮实际选中=selected,后续核验=result))
    usable=[r for r in reports if r['概率可计算'] and r['后续核验']['窗口内到阈值'] is not None]
    score=sum((r['需求概率十亿分数']/SCALE-int(r['后续核验']['窗口内到阈值']))**2 for r in usable)/len(usable) if usable else None
    paths=[root/n for n in ('zenfs.run.log','block-trace.csv','block-trace.status','reads.csv','initial.csv')]
    return dict(核验通过=True,实际策略='Greedy，只读旁观，不启用新模型',场景=scenario,采样批次=count,
        候选=reports,状态计数=dict(Counter(r['后续核验']['状态'] for r in reports)),
        数值概率且标签可知样本数=len(usable),Brier分数=score,原维护结果={k:actual[k] for k in ('ZC次数','RR次数','总复制字节','总成功ZoneReset')},
        输入SHA256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        边界='同一真实原块连续轨迹；维护、退役、换块、Reset或空档即删失，未知不能当负例。'
        '三个受控场景和重复候选不是独立统计样本，Brier仅诊断，不证明泊松校准或泛化，更不代表新模型真实收益。')
