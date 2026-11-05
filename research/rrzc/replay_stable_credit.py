"""真实历史上的静态评分重放；独立整数核验，不声称新策略的RR或迁移收益。"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from analyze_joint_opportunity import fields, model_decisions, require
from analyze_recent_eta import validate_rate, MAX, SECOND

ROOT=Path(__file__).resolve().parent


def ceil_div(a,b):
    return (a+b-1)//b


def oracle(values):
    """使用Python任意精度整数独立计算，不导入C++计算结果。"""
    (zone,valid,capacity,rc,d10,d30,flags,age,threshold,known,delta,span,
     retained_known,loss10,loss30,interval10,interval30)=values
    risk,eta,retained,credit,short,average=0,MAX,0,0,MAX,MAX
    reclaimed=max(0,capacity-valid)
    if valid>=capacity or not threshold or not(flags&1) or flags&8 or flags&~15 or not(0<=d10<=d30<=rc):
        return risk,eta,retained,credit,valid,reclaimed,short,average
    if rc>=threshold:
        return 1,0,valid,valid,0,reclaimed,MAX,MAX
    if not(flags&2) or age<10*SECOND or not known or not(SECOND//2<=span<=2*SECOND) or delta>rc:
        return risk,eta,retained,credit,valid,reclaimed,short,average
    risk=1
    short=min(MAX,ceil_div((threshold-rc)*span,delta)) if delta else MAX
    average=min(MAX,ceil_div((threshold-rc)*10*SECOND,d10)) if d10 else MAX
    eta=max(short,average)
    if eta<MAX and retained_known and 10*SECOND<=interval10<=12*SECOND and \
            30*SECOND<=interval30<=32*SECOND and loss10<=loss30:
        loss=max(ceil_div(loss10*eta,interval10),ceil_div(loss30*eta,interval30))
        retained=max(0,valid-loss)
        credit=retained*10*SECOND//(10*SECOND+eta)
    return risk,eta,retained,credit,valid-credit,reclaimed,short,average


def calculate(binary,inputs):
    raw=''.join(' '.join(map(str,r))+'\n' for r in inputs)
    actual=[tuple(map(int,line.split())) for line in
            subprocess.check_output([str(binary),'replay'],input=raw,text=True).splitlines()]
    require(actual==[oracle(row) for row in inputs],'C++评分与独立整数核验不一致')
    return actual


def replay_log(binary,path):
    raw=path.read_bytes();lines=raw.decode().splitlines()
    require(not any('稳定收益模式：' in line for line in lines),'此入口只重放原近期ETA归档，不混入新运行')
    maps={name:{} for name in ('V1影子候选','近期ETA对照','统一留存')}
    for line in lines:
        for name,index in maps.items():
            if name+'：' in line:
                r=fields(line,name+'：');key=(r['轮次'],r['Zone'])
                require(key not in index,'同轮同区证据重复，拒绝取最后一条')
                index[key]=r
    rounds=model_decisions(lines);require(rounds,'未找到真实决策')
    result=[];positive=0;eligible=0
    for item in rounds:
        decision=item['决策'];inputs=[];sources=[]
        for cost in item['成本']:
            key=(cost['轮次'],cost['Zone'])
            b=maps['V1影子候选'].get(key);r=maps['近期ETA对照'].get(key)
            f=maps['统一留存'].get(key)
            require(b is not None and b['原候选资格']=='1' and f is not None,'成本行缺原资格或留存证据')
            valid,reclaim=int(cost['有效']),int(cost['回收'])
            if b['统计准入']=='1':
                require(r is not None,'统计准入却缺少同块近期核验')
                validate_rate(r,b)
                require(r['新策略']=='1' and cost['ETA']==r['新ETA'] and cost['RR抵扣']==r['新抵扣'],
                        '历史实际分支不是所登记近期ETA模型')
                values=(int(cost['Zone']),valid,valid+reclaim,int(b['当前RC']),int(b['十秒真实增量']),
                    int(b['三十秒真实增量']),int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),
                    int(r['阈值']),int(r['近期可知']),int(r['近期增量']),int(r['间隔']))
            else:
                require(r is None and int(cost['RR抵扣'])==0,'未知统计却存在可用评分证据')
                values=(int(cost['Zone']),valid,valid+reclaim,0,0,0,0,0,0,0,0,0)
            values+=tuple(int(f[n]) for n in ('可知','十秒减少','三十秒减少','十秒间隔','三十秒间隔'))
            require(cost['留存可知']==f['可知'],'留存证据与实际评分不一致')
            inputs.append(values);sources.append(cost)
        calculated=calculate(binary,inputs)
        rows=[]
        for values,cost,numbers in zip(inputs,sources,calculated):
            risk,eta,retained,credit,net,reclaimed,short,average=numbers
            eligible+=1;positive+=credit>0
            rows.append(dict(Zone=values[0],有效字节=values[1],回收字节=reclaimed,安全=cost['安全']=='1',
                原抵扣字节=int(cost['RR抵扣']),风险可知=bool(risk),
                短窗ETA秒=short/SECOND if short<MAX else None,
                十秒ETA秒=average/SECOND if average<MAX else None,
                稳定ETA秒=eta/SECOND if eta<MAX else None,
                预计留存字节=retained,新抵扣字节=credit,净复制字节=net))
        safe=[r for r in rows if r['安全']]
        ordered=sorted(safe,key=lambda r:(Fraction(r['净复制字节'],r['回收字节']),r['有效字节'],r['Zone']))
        greedy=min(safe,key=lambda r:(r['有效字节'],r['Zone']))['Zone'] if safe else 0
        selected=ordered[0]['Zone'] if ordered else 0
        result.append(dict(轮次=int(decision['轮次']),原实际Zone=int(decision['实际Zone']),
            冻结候选Greedy=greedy,新模型建议Zone=selected,
            建议改变=selected!=int(decision['实际Zone']),候选=rows))
    return dict(日志=str(path),SHA256=hashlib.sha256(raw).hexdigest(),轮数=len(result),
        合格候选记录数=eligible,正抵扣记录数=positive,建议改变轮数=sum(r['建议改变'] for r in result),
        重放=result,新策略真实RR次数=None,新策略真实迁移字节=None,
        边界='冻结历史上的逐轮独立评分；首个改选以后状态会分叉，不能把后续重放当作新策略运行')


def run(logs):
    source=ROOT/'tests/test_stable_credit_selector.cc'
    identity=[Path(__file__).resolve(),source,ROOT/'analyze_recent_eta.py',ROOT/'analyze_joint_opportunity.py',
              *sorted((ROOT/'model').glob('*.h'))]
    with tempfile.TemporaryDirectory(prefix='rrzc-stable-replay-') as tmp:
        binary=Path(tmp)/'unit'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(source),'-o',str(binary)],check=True,capture_output=True)
        unit=subprocess.check_output([str(binary)],text=True).strip()
        reports=[replay_log(binary,p) for p in logs]
    return dict(口径='模型修改后的静态重放，不是FEMU实验或因果收益',合成边界测试=unit,
        源码身份={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in identity},
        日志=reports,公式='稳定ETA=max(同块近期ETA,同块十秒ETA)；抵扣=预计留存×10秒/(10秒+稳定ETA)；直接按(有效量-抵扣)/回收量排序',
        限制='时间折减非校准概率；不预测突然退役，不证明避免而非推迟RR；未知证据不给收益，RR/GC原条件不变')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();require(not args.output.exists(),'拒绝覆盖已有重放')
    result=run(args.log)
    with args.output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps([{k:r[k] for k in ('日志','轮数','合格候选记录数','正抵扣记录数','建议改变轮数')}
                      for r in result['日志']],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
