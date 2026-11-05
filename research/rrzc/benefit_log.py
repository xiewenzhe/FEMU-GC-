"""独立整数重算收益预算及实际设备功能短测；不把功能联调当作性能收益。"""
import csv
from fractions import Fraction
import hashlib
import json
import re
from pathlib import Path
from analyze_v1_shadow_stream import fields,require,eta,MAX,SECOND,EXTRA
from analyze_v1_growth_stream import qualifies
from retention_log import RetentionLog


def evaluate(candidates,blocks,forecasts,estimates):
    """与C++实现分别编写；输入来自当轮已核验的原资格及同块统计。"""
    eligible=[c for c in candidates if qualifies(c)]
    out=dict(greedy=None,selected=None,switch=False,extra=0,predicted=MAX,
             budget=EXTRA,upper=0,costs={})
    if not eligible:return out
    g=min(eligible,key=lambda c:(int(c['有效字节']),int(c['Zone'])))
    gz,gv,cap=(int(g[k]) for k in ('Zone','有效字节','容量字节'))
    out.update(greedy=gz,selected=gz)
    for c in eligible:
        if int(c['容量字节'])!=cap:return out
        out['upper']=max(out['upper'],int(c['有效字节'])-gv)
    ge=estimates.get(gz)
    if ge is None:return out
    if ge<=10*SECOND:out['predicted']=ge;return out
    if not any(int(c['Zone'])!=gz and estimates.get(int(c['Zone'])) is not None and
               estimates[int(c['Zone'])]<=10*SECOND for c in eligible):return out
    best=(Fraction(gv,cap-gv),gv,MAX,gz)
    for c in eligible:
        z,v=int(c['Zone']),int(c['有效字节'])
        if z==gz or z not in blocks or blocks[z].get('统计准入')!='1' or z not in forecasts:continue
        b,f=blocks[z],forecasts[z]
        cost=dict(可知=0,ETA纳秒=MAX,预计保留字节=0,折扣抵扣字节=0,净复制字节=0,回收字节=cap-v)
        out['costs'][z]=cost
        short,long=estimates.get(z),eta(b)
        if short is None or long is None or int(b['窗口标志'])&8:continue
        t=max(short,long);cost['ETA纳秒']=t
        if t>10*SECOND:continue
        a,d=(int(f[k]) for k in ('十秒实际间隔纳秒','三十秒实际间隔纳秒'))
        l10,l30=(int(f[k]) for k in ('十秒减少字节','三十秒减少字节'))
        if f['可知']!='1' or not 10*SECOND<=a<=12*SECOND or not 30*SECOND<=d<=32*SECOND or l10>l30 or int(f['预计保留字节'])>v:continue
        loss=min(v,max((l10*t+a-1)//a,(l30*t+d-1)//d))
        kept=v-loss;credit=kept*(10*SECOND-t)//(10*SECOND)
        cost.update(可知=1,预计保留字节=kept,折扣抵扣字节=credit,净复制字节=v-credit)
        key=(Fraction(v-credit,cap-v),v,t,z)
        if credit and (key[0]<best[0] or out['switch'] and key<best):
            best=key;out.update(selected=z,switch=True,extra=v-gv,predicted=t)
    if out['switch']:
        credit=out['costs'][out['selected']]['折扣抵扣字节']
        out['budget']=min((credit*(cap-gv)-1)//cap,out['upper'])
        require(out['extra']<=out['budget'],'独立净成本与预算严格不等式矛盾')
    return out


class BenefitLog:
    def __init__(self,execute):
        self.execute=execute;self.retention=RetentionLog(False)
        self.blocks={};self.rows={};self.suggestions={};self.resolved={};self.config=[]

    def observe(self,line):
        self.retention.observe(line)
        if '收益预算配置：' in line:self.config.append(fields(line.split('，默认关闭',1)[0],'收益预算配置：'))
        for marker,target in (('V1影子候选：',self.blocks),('收益预算候选：',self.rows)):
            if marker in line:
                r=fields(line,marker);key=(r['轮次'],int(r['Zone']))
                require(key not in target,'收益预算关联输入重复');target[key]=r
        if '收益预算建议：' in line:
            r=fields(line,'收益预算建议：');require(r['轮次'] not in self.suggestions,'收益预算建议重复')
            self.suggestions[r['轮次']]=r

    def resolve(self,rid,pending,estimates,old):
        self.retention.resolve(rid,pending,estimates,old)
        blocks={z:b for (r,z),b in self.blocks.items() if r==rid}
        forecasts={z:f for (r,z),f in self.retention.candidates.items() if r==rid}
        value=evaluate(pending,blocks,forecasts,estimates)
        costs={z:c for (r,z),c in self.rows.items() if r==rid}
        require(set(costs)==set(value['costs']),'收益预算成本集合不符')
        for z,c in value['costs'].items():
            require(all(int(costs[z][k])==v for k,v in c.items()),'收益预算候选成本重算不符')
        s=self.suggestions[rid]
        expected=(int(self.execute),value['greedy'] or 0,value['selected'] or 0,int(value['switch']),
                  value['extra'],value['predicted'],EXTRA,value['budget'],value['upper'])
        keys=('执行','GreedyZone','建议Zone','会改选','额外字节','预测纳秒','原预算字节','执行预算字节','合格集合额外上界')
        require(tuple(int(s[k]) for k in keys)==expected,'收益预算建议或执行预算重算不符')
        self.resolved[rid]=value
        return value['greedy'],value['selected'],value['switch'],value['extra'],value['predicted'],None

    def budget_for(self,rid):return self.resolved[rid]['budget']

    def finish(self,count):
        require(len(self.config)==1 and self.config[0]['观察']=='1' and
                self.config[0]['执行']==str(int(self.execute)),'收益预算入口配置不符')
        require(len(self.resolved)==len(self.suggestions)==count,'收益预算轮次未闭合')
        require(all(r in self.resolved for r,z in self.rows),'存在未关联的收益预算成本')
        self.retention.finish(count)


def verify_smoke(root,mode):
    from guarded_rounds import parse
    from replay_space_emergency import replay as space_replay
    from replay_single_recovery import replay as single_replay
    from analyze_controlled_zc import ZC_STATS
    from analyze_maintenance_overlap import RR_STATS,parse_lines
    from analyze_controlled_choice import selection_view
    for name in ('run.exit','verify.exit','runner.exit','wrapper.exit'):
        require((root/name).read_text().strip()=='0','设备短测阶段失败：'+name)
    before=(root/'software.before.log').read_text()
    require(before and before==(root/'software.after.log').read_text() and
            all(s.endswith(': OK') for s in before.splitlines()),'运行期间软件身份变化')
    initial=json.loads((root/'zones.initial.json').read_text())
    require(initial['nr_zones']==len(initial['zone_list'])==32 and all(z['state']=='EMPTY' and
        z['wp']==z['slba'] and z['cap']==524288 for z in initial['zone_list']),'初始设备不全空或几何错误')
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('[ERROR]' not in raw and '状态=失败' not in raw,'功能阶段出现维护错误')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',raw))=={'20'},'原GC20%未确认')
    filtered,space=space_replay(lines);filtered,single=single_replay(filtered)
    require(not space and not any(r['存在']=='1' for r in single),'功能场景混入公共应急维护')
    active=mode=='benefit';model=BenefitLog(True) if active else RetentionLog(False)
    if not active:require('收益预算建议：' not in raw and '收益预算配置：' not in raw,'默认入口被收益预算启用')
    decisions,_,maintenance=parse(filtered,'guarded' if active else 'greedy',cost_rank=False,
        proposal_model=model,budget_for=model.budget_for if active else None)
    releases=[(i,fields(s.split('，后续固定',1)[0],'收益预算功能释放：')) for i,s in enumerate(lines,1) if '收益预算功能释放：' in s]
    require(len(releases)==1,'释放标记缺失或重复')
    number,r=releases[0]
    require(tuple(int(r[k]) for k in ('冷有效字节','热有效字节','额外字节','预读RC'))==
            (16<<20,40<<20,24<<20,3904),'功能输入与预注册不符')
    choices=[d for d in decisions if d['行号']>number and d['存在Greedy']=='1']
    require(choices,'释放后没有实际选区')
    d=choices[0];expected=r['热Zone'] if active else r['GreedyZone']
    require(d['GreedyZone']==r['GreedyZone'] and d['实际Zone']==expected and
            d['实际改选']==str(int(active)) and int(d['实际额外有效字节'])==((24<<20) if active else 0),
            '首次实际选择或额外搬运不符合功能目的')
    zc=[tuple(map(int,m.groups())) for s in lines if (m:=ZC_STATS.search(s))]
    rr=[tuple(map(int,m.groups())) for s in lines if (m:=RR_STATS.search(s))]
    require(sum(row[2] for row in zc)==len(maintenance) and all(row[2]==1 for row in zc) and
            sum(row[1] for row in zc)==sum(int(row['本轮迁移字节']) for row in maintenance),'ZC迁移及Reset账目不符')
    require(not rr,'功能短测在首次预期维护前发生独立RR，不能按预期验收')
    copied=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    require(copied and all(c['复制成功']=='1' for c in copied) and
            sum(int(c['有效字节']) for c in copied)==sum(r[1] for r in zc) and
            'RR 开始：' not in raw and 'RR 完成：' not in raw,'复制或RR账目不完整')
    events=parse_lines(selection_view(lines),64)
    require(not events['failed_resets'],'存在失败Reset')
    verify=(root/'verify.stdout.log').read_text();verifylog=(root/'zenfs.verify.log').read_text()
    require('只读校验通过：完整56MiB有效数据，24个填充文件首尾4KiB' in verify,'只读载荷核验未通过')
    require('RR 开始：' not in verifylog and not ZC_STATS.search(verifylog) and
            '独立约束决策：' not in verifylog and '[ERROR]' not in verifylog,'只读验证引入维护或错误')
    with (root/'reads.csv').open() as stream:reads=list(csv.DictReader(stream))
    require(reads and len(reads)<=480 and all(int(row['序号'])==i and int(row['计划纳秒'])==i*SECOND//32 and
        int(row['API耗时纳秒'])>0 for i,row in enumerate(reads)),'真实持续读取序列不符')
    return dict(验收通过=True,模式=mode,正常轮数=len(decisions),首次维护=d,
        ZC次数=sum(r[2] for r in zc),ZC迁移字节=sum(r[1] for r in zc),RR次数=0,
        有效载荷完整校验字节=56<<20,填充文件首尾抽检数=24,持续读取次数=len(reads),
        原始文件SHA256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()},
        边界='构造载荷的真实设备功能联调，不是RocksDB SST或YCSB性能实验；不能据此推断RR、总迁移量或吞吐量改善。原始留存采样已归档，但尚未重放完整采样发布顺序。')


def verify_pair(root):
    arms={mode:verify_smoke(root/mode/mode,mode) for mode in ('default','benefit')}
    require((root/'default/default/software.before.log').read_bytes()==
            (root/'benefit/benefit/software.before.log').read_bytes(),'两次功能测试软件清单不同')
    a,b=(arms[mode]['首次维护'] for mode in ('default','benefit'))
    require(a['GreedyZone']==b['GreedyZone'],'两组准备源区不一致')
    return dict(两次功能核验通过=True,各组=arms,
        核验源码SHA256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (Path(__file__),Path(__file__).with_name('guarded_rounds.py'))},
        验证结论='默认仍选择16MiB冷区，新入口实际选择40MiB热区；额外24MiB在收益预算及原源/尾部复核下完成真实复制与Reset。',
        不能推断='两组均在各自首次预期维护完成后停止，持续读取次数不同，不是固定请求性能配对；RR均为0不能表示节省RR，总迁移和吞吐量收益仍须YCSB闭环验证。',
        记录说明='保留原始组级核验和收尾异常不覆盖。本次重新核验修正旧尾部报告中“额外预算不变”的继承文字，数值预算、选择及维护核验条件未放宽。')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    require(not args.output.exists(),'输出已存在，拒绝覆盖')
    result=verify_pair(args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(result['验证结论']);print(result['不能推断'])
