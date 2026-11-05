"""独立预测核的高精度重算；不把冻结重放当作设备实验。"""
import argparse,copy,hashlib,json,subprocess,tempfile
from decimal import Decimal,localcontext,ROUND_CEILING
from fractions import Fraction
from pathlib import Path
from analyze_joint_opportunity import require,fields
from replay_window_credit import oracle as old_window,MAX,SECOND
from replay_expected_rr import probability
from replay_terminal import inputs,choose_v2
from analyze_terminal_ycsb import compare_three
from analyze_deadline import choose as direct_choose

ROOT=Path(__file__).resolve().parent
BLOCK_FIELDS=('zone','index','generation','id','ch','lun','plane','block','ns','epoch','rc','d10','d30','flags')

def growth(case):
    b=case['block'];r=case['report'];out=[0,0,0,0,0]
    if not r['threshold'] or not r['blocks'] or not (r['hi'] or r['lo']) or not b['flags']&1 or \
            b['flags']&8 or b['flags']&~15 or b['id']>=r['blocks'] or b['epoch']>b['ns']:return out
    last_ns,last_rc=b['ns'],b['rc'];middle=None
    for f in reversed(case['history']):
        if f['report']!=r or f['block'] is None:return out
        p=f['block']
        if any(p[k]!=b[k] for k in ('zone','index','generation','id','ch','lun','plane','block','epoch')) or \
                not p['flags']&1 or p['flags']&8 or p['flags']&~15:return out
        if p['ns']>=last_ns or p['rc']>last_rc or p['ns']<p['epoch']:return out
        last_ns,last_rc=p['ns'],p['rc'];span=b['ns']-p['ns']
        if span>4*SECOND:return out
        if middle is None:
            if span<SECOND//2:continue
            middle=p;out[1:3]=[b['rc']-p['rc'],span];continue
        if span<2*SECOND or middle['ns']-p['ns']<SECOND//2:continue
        out[0]=1;out[3:5]=[middle['rc']-p['rc'],middle['ns']-p['ns']];return out
    return out

def conditional(margin,mean,window):
    # 独立使用截断一阶矩恒等式，不采用C++归一化尾项加权算法。
    with localcontext() as ctx:
        ctx.prec=100;x=Decimal(mean.numerator)/Decimal(mean.denominator)
        term=(-x).exp();cdf=term
        for n in range(1,margin):term=term*x/n;cdf+=term
        p=1-cdf;value=Decimal(window)*margin/x*(p-term*x/margin)/p
        return max(1,min(window,int(value.to_integral_value(rounding=ROUND_CEILING))))

def live(valid,f,time):
    with localcontext() as ctx:
        ctx.prec=90;v=Decimal(valid)
        fractions=[(-(1+Decimal(loss)/v).ln()*Decimal(time)/span).exp()
                   for loss,span in ((f[4],f[5]),(f[6],f[7]))]
        return int(v*min(fractions))

def compatibility(g):
    if not g[0] or not g[2] or not g[4] or g[1]+g[3]>100000:return 0,0
    count=g[1]+g[3]
    if not count:return 1,10**9
    with localcontext() as ctx:
        ctx.prec=100;p=Decimal(g[2])/(g[2]+g[4]);term=(1-p)**count;lower=upper=Decimal(0)
        # 从零项顺序计算，与C++从众数向两侧归一化采用不同算法。
        for k in range(count+1):
            if k<=g[1]:lower+=term
            if k>=g[1]:upper+=term
            if k<count:term*=Decimal(count-k)*p/((k+1)*(1-p))
        return 1,int(min(Decimal(1),2*min(lower,upper))*10**9)

def estimate(case):
    z,v,cap=case['candidate'];b=case['block'];r=case['report'];f=case['retention']
    values=(z,v,cap,b['rc'],b['d10'],b['d30'],b['flags'],b['ns']-b['epoch'],r['threshold'],b['generation'],*f)
    base=old_window(values);risk,eta,_,_,_,reclaimed,read_span,window=base
    out=[risk,eta,0,0,v,reclaimed,0,0,0,0,0,0,0,0,0,0,0,0,0]
    if b['zone']!=z or b['epoch']>b['ns']:out[0]=0;out[1]=MAX
    if not out[0] or not v:return out
    if b['rc']>=r['threshold']:out[2:5]=[v,v,0];out[15:17]=[1,10**9];return out
    g=growth(case);out[6:11]=g
    if not g[0]:out[0]=0;out[1]=MAX;return out
    if not read_span or not window:return out
    ratios=[(b['d10'],read_span),(g[1],g[2]),(g[3],g[4])]
    num,den=min(ratios,key=lambda p:Fraction(*p));out[11:14]=[num,den,min(window,g[2]+g[4])]
    if case.get('compatible',False):
        out[17:19]=compatibility(g)
        if out[17] and out[18]>=50000000:out[13]=window
    out[1]=min(MAX,((r['threshold']-b['rc'])*den+num-1)//num) if num else MAX
    out[15:17]=[1,0]
    if not num:return out
    mean=Fraction(num*out[13],den);known,units=probability(r['threshold']-b['rc'],mean)
    out[15:17]=[known,units]
    if not known or not units:return out
    out[14]=conditional(r['threshold']-b['rc'],mean,out[13]);out[2]=live(v,f,out[14])
    if not out[2]:out[15:17]=[0,0];return out
    out[3]=out[2]*units//10**9;out[4]=v-out[3];return out

def serialize(case):
    r=case['report'];z,v,cap=case['candidate']
    rows=[' '.join(map(str,(z,v,cap,r['threshold'],r['hi'],r['lo'],r['blocks'],int(case.get('compatible',False))))),
          ' '.join(str(case['block'][k]) for k in BLOCK_FIELDS),
          ' '.join(map(str,(*case['retention'],len(case['history']))))]
    for f in case['history']:
        r=f['report'];rows.append(' '.join(map(str,(r['threshold'],r['hi'],r['lo'],r['blocks'],int(f['block'] is not None)))))
        if f['block'] is not None:rows.append(' '.join(str(f['block'][k]) for k in BLOCK_FIELDS))
    return '\n'.join(rows)+'\n'

def calculate(binary,cases):
    output=subprocess.check_output([str(binary),'重算'],input=''.join(serialize(c) for c in cases),text=True)
    actual=[list(map(int,s.split())) for s in output.splitlines()];require(len(actual)==len(cases),'重算输出数量不符')
    for c,a in zip(cases,actual):
        e=estimate(c);require(len(a)==19,'重算输出版本不符')
        exact=(0,1,5,6,7,8,9,10,11,12,13,15)
        require(all(a[i]==e[i] for i in exact),'身份、区间或ETA与独立整数重算不符')
        require(abs(a[16]-e[16])<=1 and abs(a[14]-e[14])<=2,'概率或条件时间与高精度核不符')
        require(abs(a[2]-e[2])<=1 and abs(a[3]-e[3])<=2,'比例留存或期望复制与高精度核不符')
        require(a[17]==e[17] and abs(a[18]-e[18])<=1,'速率兼容性与独立二项核不符')
        require(a[3]==a[2]*a[16]//10**9 and a[4]+a[3]==c['candidate'][1],'定点字节账目不闭合')
    return actual

def frozen_inputs(lines):
    blocks={};retention={};frames={};predictions={}
    for s in lines:
        for tag,destination in (('V1影子候选：',blocks),('窗口收益成本：',retention),('时序读预测：',predictions)):
            if tag in s:
                p=fields(s.split('，原因=',1)[0],tag);destination[int(p['轮次']),int(p['Zone'])]=p
        if '时序冻结块：' in s:
            p=fields(s,'时序冻结块：');frames.setdefault((int(p['轮次']),int(p['Zone'])),{})[int(p['帧'])]=p
    cases={}
    def report(session,threshold=1024,blocks=128):
        hi,lo=map(int,session.split('/'));return dict(threshold=int(threshold),hi=hi,lo=lo,blocks=int(blocks))
    def identity(zone,index,gen,bid,ppa,ns,epoch,rc,flags,d10=0,d30=0):
        ch,lun,plane,block=map(int,ppa.split('/'))
        return dict(zip(BLOCK_FIELDS,map(int,(zone,index,gen,bid,ch,lun,plane,block,ns,epoch,rc,d10,d30,flags))))
    for key,p in predictions.items():
        b=blocks[key];good=b['统计准入']=='1';z=key[1];v=int(b['有效字节']);f=retention[key]
        current=identity(z,z//(128<<20),b.get('设备代际',0),b.get('块编号',0),b.get('PPA','0/0/0/0'),
            b.get('快照纳秒',0),b.get('代际起点纳秒',0),b.get('当前RC',0),b.get('窗口标志',0) if good else 0,
            b.get('十秒真实增量',0),b.get('三十秒真实增量',0))
        old=[]
        # 只使用实际冻结的发布帧；缺失候选保留为空，不跨越补齐。
        ids=sorted({i for (n,_),values in frames.items() if n==key[0] for i in values})
        for i in ids:
            q=frames.get(key,{}).get(i)
            old.append(dict(report=report(q['会话'],q['阈值'],q['每区块数']) if q else report(b['设备会话']),
                block=identity(z,q['区编号'],q['代际'],q['块'],q['PPA'],q['快照'],q['起点'],q['RC'],q['标志']) if q else None))
        cases[key]=dict(candidate=(z,v,128<<20),report=report(b['设备会话']),block=current,history=old,
            retention=[int(f[k]) for k in ('留存可知','留存Zone','留存代际','留存有效','短减少','短间隔','长减少','长间隔')])
    return cases,predictions

def replay(binary,path,scope='evidence'):
    raw=path.read_bytes();lines=raw.decode().splitlines();cases,old=frozen_inputs(lines)
    for c in cases.values():c['compatible']=scope=='compatible'
    keys=list(cases);actual=calculate(binary,[cases[k] for k in keys]);results=dict(zip(keys,actual))
    needs,original=inputs(lines);plans=[]
    for n,need in needs.items():
        rows=copy.deepcopy(original.get(n,[]))
        for row in rows:
            key=n,row['z'];a=results[key]
            row.update(retained=a[2],credit=a[3],p=a[16],space=(row['cap']-a[2])*a[16]//10**9)
            c=cases[key];f=c['retention']
            if row['known']:
                # 仍要求已有历史覆盖原十秒边界，未知留存不冒充可靠预测。
                row['terminal']=live(row['v'],f,10*SECOND)
        baseline=choose_v2(original.get(n,[]),need);revised=choose_v2(rows,need)
        zero=choose_v2([dict(x,p=0,credit=0,space=0) for x in rows],need)
        direct=direct_choose(rows,need)[1]
        direct_zero=direct_choose([dict(x,p=0,credit=0,space=0) for x in rows],need)[1]
        plans.append(dict(轮次=n,原预测同边界选择=baseline['zone'] if baseline['found'] else None,
            新预测同边界选择=revised['zone'] if revised['found'] else None,
            同轮新预测RR置零选择=zero['zone'] if zero['found'] else None,
            新预测不含边界项选择=direct,新预测不含边界项RR置零选择=direct_zero))
    changed=[dict(轮次=n,Zone=z,旧RR复制=int(old[n,z]['RR复制']),新RR复制=a[3],新概率=a[16],
        新事件窗口纳秒=a[13],原始输入=cases[n,z],新重算输出=a) for (n,z),a in results.items()
        if int(old[n,z]['RR复制']) or a[3]]
    return dict(日志SHA256=hashlib.sha256(raw).hexdigest(),预测条数=len(cases),
        原正估计数=sum(int(p['RR复制'])>0 for p in old.values()),新正估计数=sum(a[3]>0 for a in actual),
        正估计比较=changed,同轮边界选择=plans,边界='仅冻结输入数值及选区重放，不是新YCSB或维护节省实测')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--scope',choices=('evidence','compatible'),default='evidence')
    p.add_argument('--controlled-root',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();require(not a.output.exists(),'拒绝覆盖已有分析')
    original=compare_three(a.root,'short')
    require(json.loads(json.dumps(original))==json.loads((a.root/'short三组核验.json').read_text()),'原始实验复核不符')
    with tempfile.TemporaryDirectory(prefix='rrzc-supported-replay-') as folder:
        binary=Path(folder)/'核验器'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I'+str(ROOT/'model'),
            str(ROOT/'tests/test_supported_forecast.cc'),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
        result={mode:replay(binary,a.root/('short-'+mode)/('short-'+mode)/'zenfs.run.log',a.scope) for mode in ('space','model')}
        controlled={}
        if a.controlled_root:
            from analyze_joint_followup import analyze_arm
            for case in ('stable','aging'):
                folder=a.controlled_root/(case+'-joint')/'opportunity'
                analyze_arm(folder,'joint',terminal=True,terminal_version=2)
                controlled[case]=replay(binary,folder/'zenfs.run.log',a.scope)
    sources=[Path(__file__),ROOT/'model/rrzc_supported_forecast.h',ROOT/'tests/test_supported_forecast.cc']
    output=dict(类型='独立预测核编译与冻结重放，未接入客机',窗口规则=a.scope,
        来源SHA256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},模式=result,原受控输入重放=controlled)
    with a.output.open('x') as stream:json.dump(output,stream,ensure_ascii=False,indent=2);stream.write('\n')
    for m,r in result.items():print(m,r['预测条数'],'旧/新正估计',r['原正估计数'],r['新正估计数'])
    print('数值核验完成；不是FEMU实验结果：'+str(a.output))

if __name__=='__main__':main()
