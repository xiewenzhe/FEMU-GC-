"""只读核验联合维护短对照；窗口内节省不等于永久避免RR或YCSB性能收益。"""
import argparse
import csv
from datetime import datetime
from fractions import Fraction
import json
from pathlib import Path
import re

from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from rrzc_rr_health import inspect


def require(ok,message):
    if not ok:raise ValueError(message)


def fields(line,tag):
    return dict(part.split('=',1) for part in line.split(tag,1)[1].strip().split('，'))


def table(path):
    with path.open() as stream:return list(csv.DictReader(stream))


def model_decisions(lines):
    # 用实际成本日志重排，避免只按“统一入口启用”就声称模型抓住机会。
    rounds={};decisions=[]
    for line in lines:
        if '统一成本：' in line:
            row=fields(line,'统一成本：');rounds.setdefault(row['轮次'],[]).append(row)
            require(int(row['净复制'])==int(row['有效'])-int(row['RR抵扣'])>=0 and
                    int(row['回收'])>0,'模型成本账目不成立')
        elif '统一决策：' in line:
            decision=fields(line,'统一决策：');rows=rounds.get(decision['轮次'],[])
            safe=[r for r in rows if r['安全']=='1']
            ordered=sorted(safe,key=lambda r:(Fraction(int(r['净复制']),int(r['回收'])),
                                             int(r['有效']),int(r['Zone'])))
            require(decision['实际存在']==str(int(bool(ordered))) and
                    int(decision['实际Zone'])==(int(ordered[0]['Zone']) if ordered else 0),
                    '实际决策不是已记录安全候选中的最优成本')
            decisions.append(dict(决策=decision,成本=rows))
    return decisions


def audit(root,mode,scenario):
    for name in ('wrapper.exit','run.exit','verify.exit'):
        require((root/name).read_text().strip()=='0','阶段未成功：'+name)
    identity=(root/'software.before.log').read_text()
    require(identity and identity==(root/'software.after.log').read_text() and
            all(row.endswith(': OK') for row in identity.splitlines()),'运行前后软件身份不一致')
    geometry=json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(geometry)==32 and all(z['state']=='EMPTY' and z['wp']==z['slba'] and
            z['cap']*512==128 << 20 for z in geometry),'不是本轮全空4GiB设备')
    raw=(root/'zenfs.run.log').read_text();lines=raw.splitlines()
    require('[ERROR]' not in raw and '状态=失败' not in raw,'出现维护错误')
    release=[i for i,line in enumerate(lines) if '联合维护释放：' in line]
    end=[i for i,line in enumerate(lines) if '联合维护观察结束：' in line]
    require(len(release)==len(end)==1 and release[0]<end[0],'观察边界缺失或重复')
    require(not ZC_STATS.search('\n'.join(lines[:release[0]])) and
            not RR_STATS.search('\n'.join(lines[:release[0]])),'准备期间发生提前维护，不能视为相同初态')
    body=lines[release[0]+1:end[0]];text='\n'.join(body)
    initial=table(root/'initial.csv');observations=table(root/'recent.csv')
    ready=[r for r in observations if r['阶段']=='释放前']
    require(len(initial)==len(ready)==2 and {r['组号'] for r in initial}=={'0','1'},'初态记录不完整')
    require([int(r['有效字节']) for r in initial]==[4 << 20,(24 if scenario=='low-yield' else 8 if scenario=='retire' else 10 if scenario=='eligible-cost' else 5) << 20],
            '有效载荷与登记不符')
    require([int(r['RC']) for r in initial]==[0,928],'初始RC不是固定真实读取')
    require(all(r['空闲百分比']=='17' and r['代际']=='0' for r in initial),'空闲或设备代际不符')
    require(int(initial[1]['十秒真实增量'])==(0 if scenario=='stopped' else 928),'同块近期读增量不符')
    for row in ready:
        require(int(row['窗口标志'])==7,'近期窗口不完整或丢失')
    qualification=[]
    sources={int(r['源Zone']) for r in initial}
    for line in body:
        match=re.search(r'ZC 候选：Zone (\d+)，无效比例 (\d+)%，有效数据 (\d+) 字节，.*选中 ([01])$',line)
        if match and int(match[1]) in sources:
            qualification.append(dict(Zone=int(match[1]),无效比例=int(match[2]),有效字节=int(match[3]),原空间资格=int(match[4])))
    reads=table(root/'reads.csv');expected=0 if scenario in ('stopped','burst-stopped') else 64 if scenario=='retire' else 128
    require(len(reads)==expected,'真实后续请求数不符')
    for n,row in enumerate(reads):
        require(int(row['序号'])==n and int(row['计划纳秒'])==n*10**9//16,'读取计划变化')
        require(0<=int(row['实际开始纳秒'])-int(row['计划纳秒'])<=500_000_000,'请求调度偏差超过0.5秒，配对证据不足')
    start_time=datetime.strptime(lines[release[0]].split()[0],'%Y/%m/%d-%H:%M:%S.%f')
    end_time=datetime.strptime(lines[end[0]].split()[0],'%Y/%m/%d-%H:%M:%S.%f')
    require(25<=(end_time-start_time).total_seconds()<26,'实际观察时长不是登记的25秒窗口')
    zc=[tuple(map(int,m.groups())) for line in body if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in body if (m:=RR_STATS.search(line))]
    copies=[fields(line,'V1后续迁移复制：') for line in body if 'V1后续迁移复制：' in line]
    require(all(row['复制成功']=='1' for row in copies),'存在复制失败')
    moved=sum(row[1] for row in zc+rr)
    require(sum(int(row['有效字节']) for row in copies)==moved,'逐文件复制与ZC/RR完成账目不符')
    resets=[tuple(map(int,m.groups())) for line in body if
            (m:=re.search(r'Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，',line))]
    require(all(after==before+1 for _,before,after in resets),'Reset代际未递增')
    require(len(resets)>=sum(row[2] for row in zc+rr),'维护Reset没有成功诊断记录')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',raw))=={'20'},'原GC20%没有得到确认')
    space=re.findall(r'GC 检查：空闲=(\d+) 字节，非空闲=(\d+) 字节，空闲比例=(\d+)%',text)
    require(space,'缺少观察期间原GC空间采样')
    health=inspect(raw)
    require(health['验收通过'] and not health['末帧仍超阈值区数'],'RR健康或末帧超阈值未清除')
    require(not re.search(r'(空间紧急回收结果|公共单区恢复维护结果)：',text),'混入应急选择')
    if mode=='direct':
        require('统一选区配置：启用=1' in raw and '回收资格=0，先选Greedy=0' in text,'直接模型或零门槛未真正启用')
        decision_rows=model_decisions(body)
        require(decision_rows,'没有观察到真实模型成本排序')
    else:
        require('统一决策：' not in raw,'Greedy组混入直接模型')
        decision_rows=[]
    verification=(root/'verify.stdout.log').read_text();verifylog=(root/'zenfs.verify.log').read_text()
    require('只读校验通过：存活候选完整载荷' in verification,'独立载荷校验未完成')
    require('[ERROR]' not in verifylog and not ZC_STATS.search(verifylog) and not RR_STATS.search(verifylog),
            '只读验证期间引入维护或错误')
    return dict(验收通过=True,模式=mode,场景=scenario,初态=initial,请求计划=[(r['序号'],r['计划纳秒']) for r in reads],
        ZC次数=sum(row[2] for row in zc),RR次数=sum(row[2] for row in rr),
        ZC复制字节=sum(row[1] for row in zc),RR复制字节=sum(row[1] for row in rr),总复制字节=moved,
        总成功ZoneReset=len(resets),物理Block擦除推导数=len(resets)*128,
        擦除口径='成功ZoneReset×已验证128物理Block/Zone；不是独立NAND硬件计数器',
        实际观察秒=(end_time-start_time).total_seconds(),RR健康=health,
        末次GC采样空闲字节=int(space[-1][0]),末次GC采样非空闲字节=int(space[-1][1]),
        末次GC采样空闲百分比=int(space[-1][2]),
        实际模型成本及决策=decision_rows,
        源候选原空间资格记录=qualification,
        结论边界='仅固定25秒后续观察窗口；不证明永久避免RR、稳态收益或YCSB性能；剩余有效/空闲状态可能不同')


def compare(base,model):
    require(base['场景']==model['场景'] and base['初态']==model['初态'] and
            base['请求计划']==model['请求计划'],'两臂初始映射/RC/代际或计划读取不匹配，不能归因')
    require(base['ZC次数']==model['ZC次数']==1,'不是同样一次ZC的机会对照，不能套用预定解释')
    return dict(配对验收通过=True,场景=base['场景'],Greedy=base,直接模型=model,
        窗口内RR减少=base['RR次数']-model['RR次数'],窗口内复制减少字节=base['总复制字节']-model['总复制字节'],
        窗口内ZoneReset减少=base['总成功ZoneReset']-model['总成功ZoneReset'],
        实际结果支持窗口内联合收益=base['RR次数']>model['RR次数'] and base['总复制字节']>model['总复制字节'],
        边界='不预设正收益；必须报告更多/更少的空间回收和剩余状态，不能外推长测百分比')


def analyze_all(root):
    results={}
    for scenario in ('opportunity','retire','stopped','low-yield'):
        results[scenario]=compare(*[audit(root/f'{scenario}-{mode}'/scenario,mode,scenario)
                                    for mode in ('greedy','direct')])
    return dict(四类配对验收通过=all(r['配对验收通过'] for r in results.values()),
                场景=results,边界='原直接模型真实受控测试；不等于新评分核已部署或YCSB已优化')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    scope=parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--scenario',
        choices=('opportunity','low-yield','stopped','retire'))
    scope.add_argument('--all',action='store_true',help='逐一核验四类真实配对，不跳过缺失或失败场景')
    parser.add_argument('--output',type=Path,help='独占创建派生分析JSON，不覆盖原始或已有结果')
    args=parser.parse_args();case=args.scenario
    result=analyze_all(args.root) if args.all else compare(*[
        audit(args.root/f'{case}-{mode}'/case,mode,case) for mode in ('greedy','direct')])
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
