"""追踪冻结预测后的同块、同代际及候选文件；只读日志，不推断反事实RR节省。"""
import argparse,hashlib,json,re
from pathlib import Path
from analyze_direct_outcomes import records,validate_prediction,seconds_between
from analyze_v1_shadow_stream import fields,require

IDENTITY=('设备会话','设备代际','块编号','PPA','代际起点纳秒')
THRESHOLD=1024

def fate(lines,block,candidates,index):
    _,_,_,observations,changes,copies,retirements=index
    start=block['行号'];zone=block['Zone'];generation=int(block['设备代际'])
    ids={r['文件编号'] for r in candidates}
    require(len(ids)==len(candidates) and sum(int(r['区内有效字节']) for r in candidates)==int(block['有效字节']),
            '初始候选文件未完整覆盖，不能据此计算退役量')
    reset=None
    for n in range(start,len(lines)):
        if f'Zone 重置诊断：Zone={zone}，' not in lines[n]:continue
        r=fields(lines[n],'Zone 重置诊断：');old,new=map(int,r['主机代际'].split('/'))
        require(r['状态']=='成功' and old==generation and new==old+1,'源Reset失败或代际不连续')
        reset=dict(行号=n+1,间隔秒=seconds_between(lines,start,n+1),前代际=old,后代际=new);break
    stop=reset['行号'] if reset else len(lines)+1
    future=[];previous_ns=int(block['快照纳秒']);previous_rc=int(block['当前RC']);continuous=True
    for r in observations[zone]:
        if not start<r['行号']<stop:continue
        same=all(r.get(k)==block.get(k) for k in IDENTITY) and r.get('统计准入')=='1'
        continuous=continuous and same
        elapsed=(int(r['快照纳秒'])-int(block['快照纳秒']))/1e9
        if continuous:
            require(int(r['快照纳秒'])>previous_ns and int(r['当前RC'])>=previous_rc,'同块RC或时间回退')
            previous_ns=int(r['快照纳秒']);previous_rc=int(r['当前RC'])
        future.append(dict(行号=r['行号'],设备间隔秒=elapsed,连续同块=continuous,
            设备代际=int(r['设备代际']),块编号=int(r['块编号']),RC=int(r['当前RC']),
            相同块累计增量=int(r['当前RC'])-int(block['当前RC']) if continuous else None,
            有效字节=int(r['有效字节'])))
    own=[r for r in copies if start<r['行号']<stop and r['源Zone']==zone]
    require(all(r['复制成功']=='1' for r in own),'源搬迁存在失败，不可算成功收益')
    # 复制日志自身不能证明映射提交，逐文件核对后续持久化提交事件。
    committed=[]
    for r in own:
        later=[n+1 for n in range(r['行号'],stop-1) if 'V1后续映射提交：' in lines[n]
               and fields(lines[n],'V1后续映射提交：')['文件编号']==r['文件编号']]
        require(later and fields(lines[later[0]-1],'V1后续映射提交：')['成功']=='1','复制后缺少成功映射提交')
        committed.append(dict(行号=r['行号'],映射提交行号=later[0],维护类型=r['维护类型'],
            文件编号=int(r['文件编号']),源Zone=int(zone),目标Zone=int(r['目标Zone']),有效字节=int(r['有效字节'])))
    retired=[]
    for r in candidates:
        for n in retirements[r['文件编号']]:
            if n>start:retired.append(dict(文件编号=int(r['文件编号']),原区片段字节=int(r['区内有效字节']),
                行号=n,间隔秒=seconds_between(lines,start,n),在源Reset前=n<stop))
    rr=[dict(行号=n+1,间隔秒=seconds_between(lines,start,n+1)) for n in range(start,stop-1)
        if re.search(r'RR 开始：Zone '+re.escape(zone)+r'，',lines[n])]
    barriers=sorted(changes[zone]+[(n,'候选文件退役，联合预测验证删失') for fid in ids
        for n in retirements[fid] if start<n<stop])
    return dict(源Zone=int(zone),源设备代际=generation,初始行号=start,初始块=int(block['块编号']),
        PPA=block['PPA'],初始RC=int(block['当前RC']),初始有效字节=int(block['有效字节']),
        后续同块观察=future,候选文件退役=sorted(retired,key=lambda r:r['行号']),
        源首次Reset=reset,源Reset前RR开始=rr,源Reset前已提交复制=committed),barriers

def analyze(text,mode):
    require(mode in ('space','model'),'仅支持本轮旧模型与修正版')
    lines=text.splitlines();index=records(lines);blocks,_,files,*_=index
    forecasts={};evidence={};costs={};decisions={}
    marker='边界方案决策：' if mode=='model' else '时序方案决策：'
    for n,s in enumerate(lines,1):
        for tag,target in (('时序读预测：',forecasts),('时间一致证据：',evidence),('时序成本：',costs)):
            if tag not in s:continue
            r=fields(s,tag);key=r['轮次'],r['Zone'];require(key not in target,'同轮预测或评分重复')
            target[key]=dict(r,行号=n)
        if marker in s:
            r=fields(s,marker);require(r['轮次'] not in decisions,'实际决策重复');decisions[r['轮次']]=r
    positive=[]
    for key,p in forecasts.items():
        if int(p['RR复制'])<=0:continue
        b=blocks[key];e=evidence[key];horizon=int(e['事件窗口纳秒'])
        require(0<horizon<=int(p['窗口'])<=10**10,'事件窗口不是原预测支撑内的真实记录')
        require(int(b['当前RC'])<THRESHOLD,'初始已到阈值，不应混入未来风险估计')
        follow,barriers=fate(lines,b,files[key],index)
        outcome=validate_prediction(b,index[3][key[1]],barriers,threshold=THRESHOLD,horizon=horizon)
        d=decisions[key[0]]
        positive.append(dict(轮次=int(key[0]),预测行号=p['行号'],已进入安全评分=key in costs and costs[key]['安全']=='1',
            本轮实际选中=d['实际存在']=='1' and d['实际Zone']==key[1],
            预测ETA秒=int(p['ETA'])/1e9,事件窗口秒=horizon/1e9,预计全部失效秒=int(e['预计存活纳秒'])/1e9,
            近期读取增量=int(p['近期增量']),近期间隔秒=int(p['近期间隔'])/1e9,
            概率十亿分数=int(p['概率十亿分数']),预计RR复制字节=int(p['RR复制']),
            验证=outcome,后续=follow))
    actual=None
    if mode=='model':
        d=decisions.get('3')
        if d and d['实际存在']=='1':
            key='3',d['实际Zone'];actual=fate(lines,blocks[key],files[key],index)[0]
    return dict(模式=mode,正估计条数=len(positive),正估计=positive,第三轮实际源后续=actual,
        已证明避免RR次数=None,
        边界='同一设备会话/Reset代际/物理块才计算RC增量；RR开始是服务时刻，不是逐块真实达阈时刻。'
             '文件退役或迁移介入须标注删失；同文件不同Zone片段不能混成重复字节。'
             '反事实ZC会改变读取和目标布局，本分析不推算另一路径真实RR、总复制或性能。')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    require(not a.output.exists(),'拒绝覆盖已有核验结果')
    from analyze_terminal_ycsb import compare_three
    paired=compare_three(a.root,'short')
    require(json.loads(json.dumps(paired))==json.loads((a.root/'short三组核验.json').read_text()),'原实验复核不符')
    modes={};hashes={}
    for mode in ('space','model'):
        path=a.root/('short-'+mode)/('short-'+mode)/'zenfs.run.log';raw=path.read_bytes()
        modes[mode]=analyze(raw.decode(),mode);hashes[str(path)]=hashlib.sha256(raw).hexdigest()
    dependencies=('analyze_direct_outcomes.py','analyze_v1_shadow_stream.py','analyze_terminal_ycsb.py')
    result=dict(原始实验=str(a.root.resolve()),原始日志SHA256=hashes,
        分析器SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        依赖分析器SHA256={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in dependencies},模式=modes)
    with a.output.open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2);out.write('\n')
    for mode,v in modes.items():
        for r in v['正估计']:print(mode,r['轮次'],r['后续']['源Zone'],json.dumps(r['验证'],ensure_ascii=False))
    print('正RR估计后续核验已保存；不等于反事实收益证明：'+str(a.output))

if __name__=='__main__':main()
