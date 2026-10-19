"""同库三组成本排序核验：保留逐文件账目与终态空间，不将RR下降当净收益。"""
import hashlib
import json
from pathlib import Path
import re
from collections import Counter,defaultdict
from analyze_competition_workload import check_trace,planned_files,MIB
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require
from guarded_rounds import parse
from analyze_tail_stream import ledger
from analyze_guarded_stream import window_account
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS

LABEL='cost-rank-20261016'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-build'

def hashes(path):return {n:d for d,n in (s.split() for s in path.read_text().splitlines())}

def analyze(run,mode,expected,lifetime=False,lifetime_trial=None):
    # 新寿命负载使用独立固定计划；旧默认核验路径及结果保持不变。
    if lifetime:
        from lifetime_plan import LABEL as family,check_trace as life_trace,final_files
        require(lifetime_trial in (None,'repeat','delay20'),'未登记的寿命复验条件')
        delay=20 if lifetime_trial=='delay20' else 0
        validate_trace=lambda ops:life_trace(ops,delay)
        if lifetime_trial:family=f'lifetime-{lifetime_trial}-20261016'
        purpose='失效时序同库对照';full_mib=912;threads=('0','1','2')
    else:
        require(lifetime_trial is None,'旧负载不能使用寿命复验条件')
        family=LABEL;validate_trace=check_trace;purpose='成本排序同库对照';full_mib=984;threads=('0','1')
    require(mode in ('greedy','eta','cost') and run.name==f'{family}-{mode}','本轮模式或路径错误')
    for n in ('mkfs.exit','run.exit','verify.exit','runner.exit'):
        require((run/n).read_text().strip()=='0',n+'未成功')
    config=dict(用途=purpose,计划业务秒数='180',RR阈值='4096',GC启动比例='20',
        回收预算字节='201326592',额外预算字节='16777216',紧迫界限秒数='10',策略=mode)
    if lifetime_trial:config['退役延迟秒']=str(delay)
    require(rows(run/'config.csv')==[config],'配置不符')
    guest=Path('/home/femu/rrzc-stream-results')/run.name
    for raw in (run/'logs.sha256').read_text().splitlines():
        digest,name=raw.split(maxsplit=1)
        require(hashlib.sha256((run/Path(name).relative_to(guest)).read_bytes()).hexdigest()==digest,'原始日志身份不符')
    require((run/'identity.initial.sha256').read_bytes()==(run/'identity.final.sha256').read_bytes(),'软件运行期间改变')
    actual=hashes(run/'identity.initial.sha256')
    require(all(actual.get(n)==d for n,d in expected.items()),'未使用同一构建')
    ops=rows(run/'operations.csv');trace=validate_trace(ops)
    # 两线程分别记录精确请求顺序，包括创建／关闭／退役，不跨线程要求调度一致。
    digests=[]
    for t in threads:
        h=hashlib.sha256()
        for r in ops:
            if r['线程']==t:h.update((json.dumps([r[k] for k in ('阶段','操作','代号','文件','偏移','字节')],ensure_ascii=False)+'\n').encode())
        digests.append(h.hexdigest())
    expected_files={}
    for g in range(2,6):expected_files.update({n:v*MIB for n,v in planned_files(g).items() if '.anchor.' in n or g==5})
    expected_files.update({f'/rrzc-stream/filler{i}.bin':256*MIB for i in range(18)})
    if lifetime:expected_files=final_files(delay)
    manifest=rows(run/'manifest.csv')
    require(len(manifest)==len(expected_files) and {r['文件']:int(r['有效字节']) for r in manifest}==expected_files,'清单不符')
    verify=(run/'verify.stdout.log').read_text()
    require(f'业务完整比对字节={full_mib*MIB}' in verify and '填充文件首4KiB抽检=18' in verify,'独立数据校验不符')
    require(not any(s in (run/'zenfs.verify.log').read_text() for s in ('[ERROR]','RR 开始：','GC 检查：')),'只读校验出现维护或错误')
    lines=(run/'zenfs.run.log').read_text().splitlines();text='\n'.join(lines)
    require('[ERROR]' not in text and '状态=失败' not in text,'真实维护错误')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',text))=={'20'},'GC条件改变')
    require([int(m[1]) for m in re.finditer(r'滚动更新提交：代号=(\d+)，',text)]==list(range(6)),'提交不完整')
    decisions,_,maintenance=parse(lines,'greedy' if mode=='greedy' else 'guarded',cost_rank=mode=='cost')
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(len(zc)==len(maintenance) and text.count('RR 开始：')==text.count('RR 完成：')==len(rr),'维护未闭合')
    require(all(int(m['本轮迁移字节'])==r[1] for m,r in zip(maintenance,zc)),'ZC账目不符')
    events=ledger(lines);copies=events['实际复制']
    require(sum(int(c['有效字节']) for c in copies)==sum(r[1] for r in zc+rr),'迁移账目不符')
    final=rows(run/'states.csv')[-1];require(int(final['经过纳秒'])>=180*10**9,'未到结束时点')
    byname=defaultdict(list)
    for c in copies:byname[c['文件名']].append(c)
    perfile={n:dict(迁移MiB=sum(int(c['有效字节']) for c in moves)/MIB,次数=len(moves),
                    维护类型=dict(Counter(c['维护类型'] for c in moves))) for n,moves in byname.items()}
    ranking=[fields(line,'成本排序核验：') for line in lines if '成本排序核验：' in line]
    return dict(核验通过=True,策略=mode,来源=str(run),请求=trace,请求身份=digests,软件身份=expected,
        完整校验MiB=full_mib,排序改变次数=sum(r['排序改变']=='1' for r in ranking),
        实际改选次数=sum(d['独立核验改选'] for d in decisions),决策=decisions,排序日志=ranking,
        全程迁移MiB=sum(r[1] for r in zc+rr)/MIB,全程ZC=len(zc),全程RR=len(rr),
        账目=window_account(lines,copies,ops,0,180),逐文件=perfile,终态空间=final)

def compare(arms):
    require(set(arms)=={'greedy','eta','cost'},'缺对照组')
    baseline=arms['greedy'];deltas={}
    for mode,r in arms.items():
        require(r['请求身份']==baseline['请求身份'] and r['软件身份']==baseline['软件身份'],'请求或库不同')
        deltas[mode]={k:r['账目'][k]-baseline['账目'][k] for k in
            ('ZC次数','RR次数','总迁移MiB','重复迁移MiB','全部IO区Reset')}
        deltas[mode]['终态空闲MiB']=(int(r['终态空间']['空闲字节'])-int(baseline['终态空间']['空闲字节']))/MIB
    return dict(三组核验通过=True,各组=arms,相对Greedy=deltas,
        边界='固定180秒功能对照，单次结果不能证明稳态或统计显著性；单次少搬不等于全程净省。')
