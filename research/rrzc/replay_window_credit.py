"""窗口自适应模型的历史重放；不把冻结候选上的建议当作新策略真实收益。"""
import argparse
from collections import Counter, deque
from datetime import datetime
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from analyze_joint_opportunity import fields, model_decisions, require
from analyze_stable_credit import audit_scores

ROOT=Path(__file__).resolve().parent
SECOND=10**9
MAX=2**64-1


def oracle(row):
    """任意精度整数独立核验，输入不是预测的未来数据。"""
    z,v,cap,rc,d10,d30,flags,age,threshold,gen,known,fz,fg,fv,sl,ss,ll,ls=row
    risk,eta,retained,credit,read_span,window=0,MAX,0,0,0,0
    reclaimed=max(0,cap-v)
    def result():return risk,eta,retained,credit,v-credit,reclaimed,read_span,window
    if v>=cap or not threshold or not flags&1 or flags&8 or flags&~15 or not d10<=d30<=rc:
        return result()
    if flags&2 and age<10*SECOND or flags&4 and (age<30*SECOND or not flags&2):return result()
    if rc>=threshold:return 1,0,v,v,0,reclaimed,0,0
    risk=1
    if d10:
        if not age or not flags&2 and (age>=10*SECOND or d10!=rc):
            risk=0;return result()
        read_span=min(age,10*SECOND);delta=d10
    else:return result()
    eta=min(MAX,((threshold-rc)*read_span+delta-1)//delta)
    if not known or fz!=z or fg!=gen or fv!=v or not ss or ss>ls or ls>32*SECOND or \
            ls<SECOND//2 or sl>ll or ll>cap-v:return result()
    loss=max((sl*eta+ss-1)//ss,(ll*eta+ls-1)//ls)
    retained=max(0,v-loss)
    window=min(10*SECOND,read_span,ls)
    if eta<window:credit=retained*(window-eta)//(window+eta)
    return result()


def calculate(binary,rows):
    raw=''.join(' '.join(map(str,row))+'\n' for row in rows)
    actual=[tuple(map(int,line.split())) for line in subprocess.check_output(
        [str(binary),'replay'],input=raw,text=True).splitlines()]
    require(actual==[oracle(r) for r in rows],'窗口模型C++与独立整数核验不一致')
    return actual


def pair(row,key):return tuple(map(int,row[key].split('/')))


def clean(old,current):
    """与实际迁移标记的包围、实例及序号条件对应，不用时间相邻代替无迁移。"""
    if old['迁移可信']!='1' or current['迁移可信']!='1':return False
    if int(old['迁移实例'])==0 or old['迁移实例']!=current['迁移实例'] or \
            old['迁移创建纳秒']!=current['迁移创建纳秒']:return False
    created=int(old['迁移创建纳秒']);begin=int(old['时点纳秒']);end=int(current['时点纳秒'])
    before=int(old['迁移前纳秒']);after=int(current['迁移后纳秒'])
    a,b=pair(old,'迁移序号')[0],pair(current,'迁移序号')[1]
    ae,be=pair(old,'迁移末次结束')[0],pair(current,'迁移末次结束')[1]
    return (created<=before<=begin<=end<=after and before<=after and b>=a and be>=ae and
            ae<=before and be<=after and not pair(old,'迁移活动')[0] and
            not pair(current,'迁移活动')[1] and a==b and (not b or be<begin))


def retention(history,current):
    """只使用冻结前已发布的原始点；忙点允许跳过，Reset/迁移和大间隔不可跨过。"""
    z=int(current['Zone']);g=int(current['设备代际']);v=int(current['有效字节'])
    result=[0,z,g,v,0,0,0,0]
    if current['可知满区']!='1':return result
    last_time=int(current['时点纳秒']);last_valid=v
    for frame in reversed(history):
        old=frame.get(z)
        if old is None or old['会话']!=current['会话'] or old['设备代际']!=current['设备代际']:break
        if old['可知满区']!='1':continue
        t=int(old['时点纳秒']);ov=int(old['有效字节']);span=int(current['时点纳秒'])-t
        if any(old[k]!=current[k] for k in ('主机代际','主机序号')) or \
                t>=last_time or last_time-t>2_500_000_000 or ov<last_valid or span>32*SECOND:break
        if not clean(old,current):break
        last_time,last_valid=t,ov
        if not result[5] or result[5]<10*SECOND:result[4:6]=[ov-v,span]
        result[6:8]=[ov-v,span]
        if span>=30*SECOND:break
    result[0]=int(result[7]>=SECOND//2)
    return result


def raw_history(lines):
    history=deque(maxlen=40);pending={};frozen={};current={};last_session=None;last_after=0
    for line in lines:
        if '留存原始采样：' in line:
            r=fields(line,'留存原始采样：');z=int(r['Zone'])
            if r['阶段']=='采样':
                require(z not in pending,'原始采样发布前同区重复')
                pending[z]=r
            else:
                key=(int(r['轮次']),z);require(key not in current,'选区原始点重复');current[key]=r
        elif '独立近期采样：' in line:
            r=fields(line,'独立近期采样：')
            if r['可用']!='1' or not pending:history.clear()
            else:
                one=next(iter(pending.values()));session=one['会话'];after=int(one['迁移后纳秒'])
                require(all(p['会话']==session and int(p['迁移后纳秒'])==after for p in pending.values()),
                        '同一原始帧的会话或迁移时间不一致')
                if session!=last_session or after<=last_after:history.clear()
                history.append(pending);last_session,last_after=session,after
            pending={}
        elif '独立近期采样冻结：' in line:
            r=fields(line,'独立近期采样冻结：');frozen[int(r['轮次'])]=list(history)
    return frozen,current


def first_maintenance(lines,start,zone):
    """原轨迹上的先发生事件；遇Reset即停止，绝不跨代际追认后来的RR。"""
    active={}
    for n in range(start+1,len(lines)):
        line=lines[n];parts=line.split(maxsplit=2)
        thread=parts[1] if len(parts)>2 else ''
        if match:=re.search(r'RR 开始：Zone (\d+)，',line):
            active[thread]='RR'
            if int(match[1])==zone:return dict(事件='RR先开始',行号=n+1)
        elif 'GC 选区汇总：' in line:active[thread]='ZC'
        if 'V1后续迁移复制：' in line:
            r=fields(line,'V1后续迁移复制：')
            if int(r['源Zone'])==zone:return dict(事件=active.get(thread,'未知')+'先复制',行号=n+1)
        if match:=re.search(r'Zone 重置诊断：Zone=(\d+)，状态=(成功|失败)，',line):
            if int(match[1])==zone:return dict(事件='Reset先发生，停止同代际关联',行号=n+1)
        if any(x in line for x in ('RR 完成：','RR 未完成：','主机端 RR 检查失败：','ZC 统计：')):
            active.pop(thread,None)
    return dict(事件='日志结束前没有源维护证据',行号=None)


def replay_log(binary,path,mode):
    raw=path.read_bytes();lines=raw.decode().splitlines()
    audit_scores(lines,mode)
    frozen,current=raw_history(lines)
    blocks={};rates={};decision_lines={}
    for n,line in enumerate(lines):
        for tag,index in (('V1影子候选',blocks),('近期ETA对照',rates)):
            if tag+'：' in line:
                r=fields(line.split('，原因=',1)[0],tag+'：');index[int(r['轮次']),int(r['Zone'])]=r
        if '统一决策：' in line:
            r=fields(line,'统一决策：');decision_lines[int(r['轮次'])]=n
    reports=[];rows_count=positive=partial=0
    for item in model_decisions(lines):
        decision=item['决策'];round_id=int(decision['轮次']);inputs=[];sources=[]
        require(round_id in frozen,'缺冻结前原始采样历史')
        for cost in item['成本']:
            z=int(cost['Zone']);v=int(cost['有效']);cap=v+int(cost['回收']);key=round_id,z
            b=blocks[key];p=current.get(key)
            require(p is not None,'缺选区时原始留存观察')
            f=retention(frozen[round_id],p)
            if b['统计准入']=='1':
                r=rates[key];gen=int(b['设备代际'])
                values=(z,v,cap,int(b['当前RC']),int(b['十秒真实增量']),int(b['三十秒真实增量']),
                    int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),int(r['阈值']),gen,*f)
            else:values=(z,v,cap,0,0,0,0,0,0,0,*f)
            inputs.append(values);sources.append(cost)
        results=calculate(binary,inputs);rows=[]
        for values,cost,result in zip(inputs,sources,results):
            risk,eta,retained,credit,net,reclaimed,read_span,window=result
            rows_count+=1;positive+=credit>0;partial+=bool(values[10] and values[-1]<30*SECOND)
            rows.append(dict(Zone=values[0],有效字节=values[1],回收字节=reclaimed,安全=cost['安全']=='1',
                风险可知=bool(risk),RC=values[3],块编号=blocks[round_id,values[0]].get('块编号'),
                设备代际=values[9],ETA纳秒=eta,RR抵扣字节=credit,净复制字节=net,
                预计留存字节=retained,读历史纳秒=read_span,收益窗口纳秒=window,
                留存可知=bool(values[10]),留存短间隔=values[-3],留存长间隔=values[-1]))
        safe=[r for r in rows if r['安全']]
        ordered=sorted(safe,key=lambda r:(Fraction(r['净复制字节'],r['回收字节']),r['有效字节'],r['Zone']))
        proposed=ordered[0]['Zone'] if ordered else 0;actual=int(decision['实际Zone'])
        greedy=min(safe,key=lambda r:(r['有效字节'],r['Zone']))['Zone'] if safe else 0
        entry=dict(轮次=round_id,原实际Zone=actual,新建议Zone=proposed,冻结Greedy=greedy,
            相对原实际改选=proposed!=actual,相对冻结Greedy改选=proposed!=greedy,候选=rows)
        if proposed and proposed!=actual:
            start=decision_lines[round_id];event=first_maintenance(lines,start,proposed)
            if event['行号']:
                def stamp(i):return datetime.strptime(lines[i].split()[0],'%Y/%m/%d-%H:%M:%S.%f')
                event['距选区秒']=(stamp(event['行号']-1)-stamp(start)).total_seconds()
            entry['原轨迹后续']=event
        reports.append(entry)
    return dict(日志=str(path),原策略=mode,SHA256=hashlib.sha256(raw).hexdigest(),轮数=len(reports),
        合格候选记录数=rows_count,正抵扣记录数=positive,短于三十秒的可用留存数=partial,
        相对原实际改选轮数=sum(r['相对原实际改选'] for r in reports),
        相对冻结Greedy改选轮数=sum(r['相对冻结Greedy改选'] for r in reports),
        改选后原轨迹先发生事件=dict(Counter(r['原轨迹后续']['事件'] for r in reports if '原轨迹后续' in r)),
        改选所关联的原轨迹独立事件数=len({r['原轨迹后续']['行号'] for r in reports
            if '原轨迹后续' in r and r['原轨迹后续']['行号'] is not None}),
        重放=reports,新策略真实RR次数=None,新策略真实迁移字节=None,
        边界='旧日志没有新模型Store发布序号，使用冻结日志前已完成发布的原始帧保守重建；'
             '不使用未来结果调参，首个改选后状态会分叉，后续事件不等于新策略效果；'
             'RR先发生也不能证明同一物理块的ETA准确，最大块身份可能改变；'
             '多个改选轮次可能关联同一次RR，不能累加声称避免RR次数')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--greedy',type=Path,required=True);parser.add_argument('--stable',type=Path,required=True)
    parser.add_argument('--controlled-root',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖已有重放')
    with tempfile.TemporaryDirectory(prefix='rrzc-window-replay-') as tmp:
        binary=Path(tmp)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_window_credit_selector.cc'),'-o',str(binary)],check=True)
        unit=subprocess.check_output([str(binary)],text=True).strip()
        reports=[replay_log(binary,args.greedy,'greedy'),replay_log(binary,args.stable,'stable')]
        controlled=[]
        if args.controlled_root:
            for case in ('opportunity','retire','stopped','low-yield'):
                for mode in ('greedy','stable'):
                    r=replay_log(binary,args.controlled_root/(case+'-'+mode)/case/'zenfs.run.log',mode)
                    r['受控场景']=case;controlled.append(r)
    result=dict(口径='旧真实日志的静态重放，不是新YCSB运行',合成测试=unit,日志=reports,受控反例重放=controlled,
        公式='直接最小化(有效量-抵扣)/回收量；抵扣=预计留存×max(0,(W-ETA)/(W+ETA))；'
             'W=min(原十秒维护尺度,同块读历史长度,同代际连续留存历史长度)',
        源码身份={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__).resolve(),ROOT/'tests/test_window_credit_selector.cc',
                      *sorted((ROOT/'model').glob('*.h'))]})
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='重放'} for r in reports+controlled],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
