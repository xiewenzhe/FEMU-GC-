"""独立核验持续读修正；旧冻结轨迹重放不冒充新设备运行或RR节省。"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from analyze_joint_opportunity import fields,model_decisions,require
from analyze_window_credit import audit_scores,exact_history
from analyze_recent_eta import validate_rate
from replay_window_credit import oracle as window_oracle,retention,SECOND,MAX

ROOT=Path(__file__).resolve().parent


def oracle(row):
    """使用任意精度整数，与C++独立计算，不读取未来维护或删除结果。"""
    base=row[:18];known,delta,span=row[18:]
    z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls=base
    risk,average,retained,credit,net,reclaimed,read_span,window=window_oracle(base)
    old_credit=credit;eta=average;recent=MAX
    def result():return risk,eta,retained,credit,net,reclaimed,read_span,window,average,recent,old_credit
    if not risk or rc>=threshold or not read_span:return result()
    retained=credit=0;net=v
    if not known or not SECOND//2<=span<=2*SECOND or delta>rc or span>age or \
            span<=read_span and delta>d10:
        risk=0;eta=MAX;return result()
    recent=min(MAX,((threshold-rc)*span+delta-1)//delta) if delta else MAX
    eta=max(average,recent)
    if eta==MAX or not window:return result()
    retained=max(0,v-max((sl*eta+ss-1)//ss,(ll*eta+ls-1)//ls))
    if eta<window:credit=retained*(window-eta)//(window+eta)
    net=v-credit
    return result()


def calculate(binary,rows):
    raw=''.join(' '.join(map(str,r))+'\n' for r in rows)
    actual=[tuple(map(int,s.split())) for s in subprocess.check_output(
        [str(binary),'replay'],input=raw,text=True).splitlines()]
    require(actual==[oracle(r) for r in rows],'持续读修正C++与独立整数核验不一致')
    return actual


def replay_log(binary,path):
    raw=path.read_bytes();lines=raw.decode().splitlines()
    audit_scores(lines,'window')
    history,current,_=exact_history(lines)
    blocks={};rates={}
    for line in lines:
        for tag,index in (('V1影子候选',blocks),('近期ETA对照',rates)):
            if tag+'：' in line:
                r=fields(line.split('，原因=',1)[0],tag+'：')
                key=int(r['轮次']),int(r['Zone']);require(key not in index,'候选或同块诊断重复');index[key]=r
    reports=[];positives=old_positives=0
    for item in model_decisions(lines):
        n=int(item['决策']['轮次']);inputs=[];source=[]
        for cost in item['成本']:
            z=int(cost['Zone']);key=n,z;b=blocks[key];v=int(cost['有效']);cap=v+int(cost['回收'])
            f=[0]*8
            if b['统计准入']=='1':
                r=rates[key];validate_rate(r,b)
                p=current.get(key)
                if p is not None and int(p['有效字节'])==v:f=retention(history[n],p)
                g=(int(r['近期可知']),int(r.get('近期增量',0)),int(r.get('间隔',0)))
                values=(z,v,cap,int(b['当前RC']),int(b['十秒真实增量']),int(b['三十秒真实增量']),
                    int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),int(r['阈值']),
                    int(b['设备代际']),*f,*g)
            else:values=(z,v,cap,0,0,0,0,0,0,0,*f,0,0,0)
            inputs.append(values);source.append(cost)
        rows=[]
        for values,cost,out in zip(inputs,source,calculate(binary,inputs)):
            risk,eta,live,credit,net,reclaim,read_span,window,average,recent,old_credit=out
            positives+=credit>0;old_positives+=old_credit>0
            rows.append(dict(Zone=values[0],有效字节=values[1],安全=cost['安全']=='1',回收字节=reclaim,
                设备代际=values[9],块编号=blocks[n,values[0]]['块编号'],RC=values[3],
                近期可知=bool(values[18]),近期增量=values[19],近期间隔=values[20],
                平均ETA=average,近期ETA=recent,修正ETA=eta,原抵扣=old_credit,修正抵扣=credit,
                净复制=net,预计留存=live,风险可知=bool(risk),读历史=read_span,支撑窗口=window))
        safe=[r for r in rows if r['安全']]
        ranked=sorted(safe,key=lambda r:(Fraction(r['净复制'],r['回收字节']),r['有效字节'],r['Zone']))
        selected=ranked[0]['Zone'] if ranked else 0
        greedy=min(safe,key=lambda r:(r['有效字节'],r['Zone']))['Zone'] if safe else 0
        actual=int(item['决策']['实际Zone'])
        reports.append(dict(轮次=n,原实际Zone=actual,新建议Zone=selected,同轮Greedy=greedy,
            相对原实际变化=selected!=actual,相对同轮Greedy变化=selected!=greedy,候选=rows))
    return dict(日志=str(path),日志SHA256=hashlib.sha256(raw).hexdigest(),轮数=len(reports),
        原正抵扣记录数=old_positives,修正正抵扣记录数=positives,
        相对原实际变化轮数=sum(r['相对原实际变化'] for r in reports),
        相对同轮Greedy变化轮数=sum(r['相对同轮Greedy变化'] for r in reports),重放=reports,
        新策略真实RR次数=None,新策略真实迁移字节=None,
        边界='冻结候选、原安全标志及已发布历史上的静态重放；状态分叉后不可作为新策略闭环结果。'
             '不使用未来读取、退役或RR结果；变少的抵扣不等于已经减少实际迁移，也可能错失机会。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-log',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖原始或已有结果')
    with tempfile.TemporaryDirectory(prefix='rrzc-persistent-') as tmp:
        binary=Path(tmp)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_persistent_credit_selector.cc'),'-o',str(binary)],check=True)
        unit=subprocess.check_output([str(binary)],text=True).strip()
        reports=[replay_log(binary,p) for p in args.window_log]
    files=[Path(__file__).resolve(),ROOT/'model/rrzc_persistent_credit_selector.h',
        ROOT/'model/rrzc_window_credit_selector.h',ROOT/'model/rrzc_v1_growth.h',
        ROOT/'analyze_window_credit.py',ROOT/'analyze_recent_eta.py',ROOT/'replay_window_credit.py',
        ROOT/'tests/test_persistent_credit_selector.cc']
    result=dict(口径='持续读修正第一阶段：实际旧日志的静态重放，不是新YCSB实验',单元测试=unit,
        修正='仍直接最小化净复制/回收；ETA取同块近期与原十秒或Reset前缀预测中较长者；'
             '近期未知或停止时不抵扣，留存与支撑窗口不变；不是校准概率，不保证未来负载持续',
        源码身份={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},日志=reports)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='重放'} for r in reports],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
