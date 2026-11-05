"""期望RR成本原型的独立数值核验和冻结重放；不把建议选区称为闭环收益。"""
import argparse
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from analyze_joint_opportunity import fields, model_decisions, require
from analyze_recent_eta import validate_rate
from analyze_window_credit import audit_scores, exact_history
from replay_persistent_credit import oracle as persistent_oracle
from replay_window_credit import retention, raw_history, SECOND, MAX

ROOT=Path(__file__).resolve().parent
SCALE=10**9


def probability(margin,mean):
    """独立使用高精度十进制从零项累加CDF，不复刻C++边界项递推。"""
    if not margin:return 1,SCALE
    if margin>100000 or mean<0:return 0,0
    if not mean:return 1,0
    with localcontext() as ctx:
        ctx.prec=80
        value=Decimal(mean.numerator)/Decimal(mean.denominator)
        # 此处margin至多十万；均值至少百万时Chernoff界远小于定点分辨率。
        if value>=1000000:return 1,SCALE-1
        term=(-value).exp();cdf=term
        for n in range(1,margin):
            term=term*value/n;cdf+=term
        p=max(Decimal(0),min(Decimal(1),1-cdf))
        return 1,min(SCALE-1,int(p*SCALE))


def oracle(row):
    old=persistent_oracle(row)
    risk,eta,live,previous,_,reclaim,read_span,window=old[:8]
    z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs=row
    known,units=0,0
    if risk and rc>=threshold:known,units=1,SCALE
    elif risk and eta!=MAX and window and live:
        mean=min(Fraction(d10,read_span),Fraction(gd,gs))*window
        known,units=probability(threshold-rc,mean)
    credit=live*units//SCALE
    return risk,eta,live,credit,v-credit,reclaim,window,known,units,previous


def calculate(binary,rows):
    raw=''.join(' '.join(map(str,row))+'\n' for row in rows)
    actual=[tuple(map(int,s.split())) for s in subprocess.check_output(
        [str(binary),'replay'],input=raw,text=True).splitlines()]
    require(len(actual)==len(rows),'期望成本核输出数量不符')
    for row,out in zip(rows,actual):
        expected=oracle(row)
        require(out[:3]==expected[:3] and out[5:8]==expected[5:8] and out[9]==expected[9],
            '期望成本核的原证据/留存与独立核验不符')
        # 两种不同数值算法允许十亿分之一的量化边界误差；字节账目必须精确。
        require(abs(out[8]-expected[8])<=1,'需求概率与独立十进制CDF不符')
        require(out[3]==out[2]*out[8]//SCALE and out[4]+out[3]==row[1],
            '概率定点化后的字节账目不一致')
    return actual


def inputs_from_log(path,mode):
    lines=path.read_text().splitlines()
    require(mode in ('greedy','window','persistent'),'未知冻结输入模式')
    if mode=='greedy':
        # 仅供只读采证：原Greedy不发布窗口Store，保守使用冻结前已完成的原始帧。
        audit_scores(lines,'greedy');history,current=raw_history(lines)
    else:
        audit_scores(lines,'window',persistent=mode=='persistent')
        history,current,_=exact_history(lines)
    blocks={};rates={}
    for line in lines:
        for tag,index in (('V1影子候选',blocks),('近期ETA对照',rates)):
            if tag+'：' in line:
                item=fields(line.split('，原因=',1)[0],tag+'：')
                key=int(item['轮次']),int(item['Zone'])
                require(key not in index,'原始同块输入重复');index[key]=item
    output=[]
    for decision in model_decisions(lines):
        n=int(decision['决策']['轮次']);rows=[]
        for cost in decision['成本']:
            z=int(cost['Zone']);key=n,z;b=blocks[key]
            v=int(cost['有效']);cap=v+int(cost['回收']);f=[0]*8
            if b['统计准入']=='1':
                rate=rates[key];validate_rate(rate,b)
                point=current.get(key)
                if point is not None and int(point['有效字节'])==v:f=retention(history[n],point)
                values=(z,v,cap,int(b['当前RC']),int(b['十秒真实增量']),int(b['三十秒真实增量']),
                    int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),int(rate['阈值']),
                    int(b['设备代际']),*f,int(rate['近期可知']),int(rate.get('近期增量',0)),int(rate.get('间隔',0)))
            else:values=(z,v,cap,0,0,0,0,0,0,0,*f,0,0,0)
            rows.append((values,cost['安全']=='1'))
        output.append((decision,rows))
    return output


def replay_log(binary,path,mode='persistent'):
    reports=[]
    for decision,inputs in inputs_from_log(path,mode):
        costs=calculate(binary,[r for r,_ in inputs]);rows=[]
        for (row,safe),cost in zip(inputs,costs):
            rows.append(dict(Zone=row[0],有效字节=row[1],安全=safe,RC=row[3],近期增量=row[19],
                ETA=cost[1],预计留存=cost[2],需求概率十亿分数=cost[8],概率可计算=bool(cost[7]),
                RR抵扣=cost[3],净复制=cost[4],回收字节=cost[5],支撑窗口=cost[6],原持续读抵扣=cost[9]))
        safe=[r for r in rows if r['安全']]
        ranked=sorted(safe,key=lambda r:(Fraction(r['净复制'],r['回收字节']),r['有效字节'],r['Zone']))
        reports.append(dict(轮次=int(decision['决策']['轮次']),原实际Zone=int(decision['决策']['实际Zone']),
            新建议Zone=ranked[0]['Zone'] if ranked else 0,候选=rows))
    return dict(日志=str(path),日志SHA256=hashlib.sha256(path.read_bytes()).hexdigest(),重放=reports,
        新策略真实RR次数=None,新策略真实迁移字节=None,
        边界='只使用选区时已发布的同块读取和有效量历史；首个改选后状态分叉，不能推导新闭环结果。'
        '概率以泊松持续读取为假设，尚未校准；不包含未来删除、其他ZC或RR实际排队时间。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,action='append',required=True)
    parser.add_argument('--mode',choices=('window','persistent'),default='persistent')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖已有分析')
    with tempfile.TemporaryDirectory(prefix='rrzc-expected-replay-') as tmp:
        binary=Path(tmp)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/test_expected_rr_selector.cc'),'-o',str(binary)],check=True)
        unit=subprocess.check_output([str(binary)],text=True).strip()
        reports=[replay_log(binary,p,args.mode) for p in args.log]
    files=[ROOT/'model/rrzc_expected_rr_selector.h',Path(__file__).resolve(),ROOT/'tests/test_expected_rr_selector.cc']
    result=dict(口径='期望RR成本原型的编译、核验和静态重放，不是新设备或YCSB实验',单元测试=unit,
        假设='同块近期及十秒平均中较慢速率在窗口内近似持续，读取计数暂以泊松过程近似；未校准。'
        '留存继续使用已观察退役速率，不预知突发文件删除。',
        源码身份={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},日志=reports)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(输出=str(args.output),日志数=len(reports),边界='静态建议，不是实际RR节省'),ensure_ascii=False))


if __name__=='__main__':main()
