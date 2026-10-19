"""独立按两个历史端点重算速率与影子建议；不覆盖旧增长检查器。"""
import json
from collections import Counter
from analyze_rocksdb_observe import analyze,factor_summary
from analyze_rocksdb_recent import sampling_audit
from analyze_rocksdb_pressure import gc_audit
from analyze_short_shadow_stream import choose_short
from analyze_v1_shadow_stream import fields,require,MAX,SECOND
from analyze_stream import rows
from analyze_v1_growth_stream import qualifies

LABEL='rocksdb-pair-rate-20261016'

def rate(block,endpoints,count):
    """只读取传入的事前端点，按整数运算复核生产模块。"""
    out=dict(增量可知=0,真实增量=0,间隔纳秒=0,最旧龄纳秒=0,最新龄纳秒=0,
             预测达阈值纳秒=MAX,界限内=0,原因='缺少最近两个采样点')
    if not block or block.get('统计准入')!='1' or not int(block.get('窗口标志','0'))&1:
        out['原因']='当前统计或参数无效';return out
    if count!=2:return out
    if set(endpoints)!={'0','1'}:out['原因']='端点区缺失';return out
    a,b=endpoints['0'],endpoints['1']
    def identity(p):
        return tuple(p[k] for k in ('会话','阈值','块数','代际','块编号','PPA','区编号','起点纳秒'))
    current=dict(会话=block['设备会话'],阈值='4096',块数='64',代际=block['设备代际'],
        块编号=block['块编号'],PPA=block['PPA'],区编号=str(int(block['Zone'])//(256<<20)),
        起点纳秒=block['代际起点纳秒'])
    if identity(a)!=identity(b) or identity(b)!=identity(current) or any(not int(p['标志'])&1 for p in (a,b)):
        out['原因']='会话代际或块身份变化';return out
    t0,t1,t2=int(a['快照纳秒']),int(b['快照纳秒']),int(block['快照纳秒'])
    r0,r1,r2=int(a['RC']),int(b['RC']),int(block['当前RC'])
    if not int(a['起点纳秒'])<=t0<t1<t2 or not r0<=r1<=r2:
        out['原因']='时间或读数不单调';return out
    out.update(间隔纳秒=t1-t0,最旧龄纳秒=t2-t0,最新龄纳秒=t2-t1)
    if t1-t0<SECOND//2:out['原因']='双点间隔过近';return out
    if t1-t0>2*SECOND or t2-t0>2*SECOND:
        out['原因']='双点间隔或最旧端点过旧';return out
    out.update(增量可知=1,真实增量=r1-r0)
    if r2>=4096:
        out.update(预测达阈值纳秒=0,界限内=1,原因='同块当前已达阈值');return out
    if r1==r0:out['原因']='双点窗口无读取';return out
    eta=min(MAX,((4096-r2)*(t1-t0)+(r1-r0)-1)//(r1-r0))
    out.update(预测达阈值纳秒=eta,界限内=int(eta<=10*SECOND),
        原因='双点同块速率支持界限内达阈值' if eta<=10*SECOND else '双点同块速率不足以支持界限内达阈值')
    return out

def pair_audit(lines):
    cands=[];blocks={};endpoints={};rates={};old={};decisions=[];observations=[]
    for line in lines:
        if '简单双因素候选：' in line:cands.append(fields(line,'简单双因素候选：'))
        if 'V1影子候选：' in line:
            b=fields(line,'V1影子候选：');require(b['Zone'] not in blocks,'重复当前点');blocks[b['Zone']]=b
        if 'V1增长候选：' in line:
            g=fields(line,'V1增长候选：');require(g['Zone'] not in old,'重复旧增长点');old[g['Zone']]=g
        if '双点速率端点：' in line:
            p=fields(line,'双点速率端点：');parts=endpoints.setdefault(p['Zone'],{})
            require(p['位置'] in ('0','1') and p['位置'] not in parts,'端点重复或位置非法');parts[p['位置']]=p
        if '双点速率候选：' in line:
            r=fields(line,'双点速率候选：');require(r['Zone'] not in rates,'重复双点候选');rates[r['Zone']]=r
        if '双点速率建议：' in line:
            d=fields(line,'双点速率建议：');rid=d['轮次'];require(int(rid)==len(decisions)+1,'建议轮次不连续')
            zones={c['Zone'] for c in cands}
            require(len(zones)==len(cands) and zones==set(blocks)==set(rates),'候选记录缺失')
            estimates={}
            for c in cands:
                z=c['Zone'];r=rates[z];b=blocks[z];ends=endpoints.get(z,{})
                require(r['轮次']==b['轮次']==rid and all(p['轮次']==rid for p in ends.values()),'端点轮次不符')
                require(r['端点数'] in ('0','1','2') and len(ends)<=int(r['端点数']),'端点数量矛盾')
                require(r['原候选资格']==str(int(qualifies(c))) and r['统计准入']==b.get('统计准入','0'),'候选或统计准入被修改')
                expected=rate(b,ends,int(r['端点数']))
                require(all(r[k]==str(v) for k,v in expected.items()),'双点证据重算不符')
                estimates[int(z)]=int(r['预测达阈值纳秒']) if r['增量可知']=='1' else None
                observations.append(dict(轮次=rid,Zone=z,合格=qualifies(c),
                    是Greedy=z==d['GreedyZone'],旧增量可知=None,双点证据=expected,
                    当前RC=int(b['当前RC']) if '当前RC' in b else None,有效字节=int(c['有效字节'])))
            greedy,suggest,would,extra,eta,reason=choose_short(cands,estimates)
            require(tuple(int(d[k]) for k in ('存在Greedy','GreedyZone','建议Zone','会改选','额外有效字节','预测达阈值纳秒','实际改选'))==
                    (int(greedy is not None),greedy or 0,suggest or 0,int(would),extra,eta,0),'双点影子建议重算不符')
            require(d['原因']==reason,'双点建议原因不符');decisions.append(d)
            # 旧增长日志在建议之后出现，留到原决策结束再关联。
        if '独立约束决策：' in line:
            require(decisions and decisions[-1]['轮次']==fields(line,'独立约束决策：')['轮次'],'缺双点建议')
            rid=decisions[-1]['轮次']
            for o in observations:
                if o['轮次']==rid:
                    require(o['Zone'] in old,'旧增长日志缺失');o['旧增量可知']=old[o['Zone']]['增量可知']=='1'
            cands=[];blocks={};endpoints={};rates={};old={}
    require(not (cands or blocks or endpoints or rates or old),'最后轮次未闭合')
    eligible=[o for o in observations if o['合格']]
    return dict(轮次=len(decisions),合格候选观察数=len(eligible),
        旧增量可知数=sum(o['旧增量可知'] for o in eligible),
        双点增量可知数=sum(o['双点证据']['增量可知'] for o in eligible),
        双点紧迫候选数=sum(o['双点证据']['界限内'] for o in eligible),
        Greedy可知数=sum(o['是Greedy'] and o['双点证据']['增量可知'] for o in eligible),
        影子建议改选数=sum(d['会改选']=='1' for d in decisions),实际改选数=0,
        未知原因=dict(Counter(o['双点证据']['原因'] for o in eligible if not o['双点证据']['增量可知'])),
        候选观察=observations,影子建议=decisions,
        边界='只验证估速与原速率排序，不执行新建议，也未做新建议的尾部复核；不是避免RR或性能收益证明')

def audit(root):
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'运行期间生产身份变化')
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    run=root/'运行归档'/LABEL;result=analyze(run,expected,paired=True)
    lines=(run/'zenfs.run.log').read_text().splitlines()
    require(any('双点速率配置：启用=1' in s for s in lines),'双点入口未生效')
    result['双点速率核验']=pair_audit(lines)
    result['原增长与后台采样']=sampling_audit(lines)
    result['原GC候选覆盖']=gc_audit(lines);result['事前信号汇总']=factor_summary(result)
    previous=root.parent/'rocksdb-recent-20261016/运行归档/rocksdb-recent-20261016'
    for name,keys in (('reads.csv',('序号','键号')),('writes.csv',('序号','起始键序号','键数','起始版本'))):
        require([[r[k] for k in keys] for r in rows(run/name)]==[[r[k] for k in keys] for r in rows(previous/name)],'相对上一轮逻辑请求改变')
    result['与上一轮逻辑请求逐项一致']=True
    return result

if __name__=='__main__':
    import sys
    from pathlib import Path
    require(len(sys.argv)==2,'需要固定归档目录');root=Path(sys.argv[1]).resolve()
    require(root.name==LABEL and not (root/'独立核验.json').exists(),'目录不符或已核验')
    (root/'独立核验.json').write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
