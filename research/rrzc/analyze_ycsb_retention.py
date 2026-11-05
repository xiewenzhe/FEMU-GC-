"""核验真实YCSB-C++数据、完整维护账目和留存选择；不把RR下降单独当收益。"""
import hashlib
import json
import re
from datetime import datetime,timedelta
from pathlib import Path
from analyze_v1_shadow_stream import require,fields
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from guarded_rounds import parse
from retention_log import RetentionLog
from ycsb_throughput import throughput

MIB=1<<20
MODES=('greedy','v1','cost','retention')
GUEST=Path('/home/femu/rrzc-ycsb-results/20261017')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(1<<20):h.update(block)
    return h.hexdigest()

def measurements(text,expected_ops,expected_types=('READ','UPDATE')):
    lines=[s for s in text.splitlines() if ' sec: ' in s and 'operations;' in s]
    require(lines,'缺YCSB状态输出')
    final=lines[-1];out={}
    for op,body in re.findall(r'\[([^:]+):([^\]]+)\]',final):
        pairs=re.findall(r'(?:^|\s)(Count|Avg|95|99)=([\d.]+)(?=\s|$)',body)
        require(len(pairs)==4,'真实均值或分位字段重复/缺失')
        row=dict(pairs)
        require(set(row)=={'Count','Avg','95','99'},'缺真实均值或分位')
        require('FAILED' not in op,'存在失败操作')
        out[op]=dict(次数=int(row['Count']),平均微秒=float(row['Avg']),p95微秒=float(row['95']),p99微秒=float(row['99']))
    require(set(out)==set(expected_types) and sum(v['次数'] for v in out.values())==expected_ops,'业务请求数不符')
    return out

def independent_profile(properties,expected_ops,ratio='80/20',update_range=None):
    # 新比例必须显式登记；旧调用仍仅接受原80/20，不默默放宽旧结果验收。
    require(ratio in ('80/20','50/50'),'独立读写比例未登记')
    read,update=('0.8','0.2') if ratio=='80/20' else ('0.5','0.5')
    if update_range is None:
        range_ok=(properties.get('rrzc.update_key_start','0')=='0'
            and properties.get('rrzc.update_key_count',properties.get('recordcount'))==properties.get('recordcount'))
    else:
        require(tuple(update_range) in ((4096,61440),(4096,258048)) and ratio=='50/50','更新区间未登记')
        range_ok=(properties.get('rrzc.update_key_start')==str(update_range[0])
            and properties.get('rrzc.update_key_count')==str(update_range[1]))
    return (expected_ops==1200000 and properties.get('rrzc.independent_keys')=='true'
        and properties.get('rrzc.read_key_count')=='4096'
        and properties.get('readproportion')==read and properties.get('updateproportion')==update
        and properties.get('requestdistribution')=='uniform' and range_ok)


def audit(root,mode,*,nowal=False,expected_ops=2400000,single_recovery=False,space_emergency=False,inherited_audit=False,independent_ratio='80/20',update_range=None,benefit_budget=False,direct_model=False):
    # 新入口必须显式登记，不让旧调用默默接受动态预算。
    require((mode=='benefit')==benefit_budget and (mode=='direct')==direct_model and
        (mode in MODES or benefit_budget or direct_model),'模式未知或新模型未显式登记')
    for name in ('business.exit','verify.exit','runner.exit'):
        require((root/name).read_text().strip()=='0',name+'未成功')
    for line in (root/'logs.sha256').read_text().splitlines():
        digest,name=line.split(maxsplit=1);relative=Path(name).relative_to(GUEST)
        require(sha(root/relative)==digest,'原始归档哈希不符：'+name)
    before=(root/'software.before.log').read_text();after=(root/'software.after.log').read_text()
    require(before==after and before and all(s.endswith(': OK') for s in before.splitlines()),'软件身份未通过')
    require((root/'策略.txt').read_text().strip()==mode,'实际策略组不符')
    properties={}
    for s in (root/'workload.properties').read_text().splitlines():
        if '=' in s and not s.startswith('#'):
            k,v=s.split('=',1);properties[k]=v
    independent=independent_profile(properties,expected_ops,independent_ratio,update_range)
    if update_range is not None:require(independent,'更新范围与登记先导不符')
    if independent_ratio!='80/20':require(independent,'新读写比例只用于已登记120万次独立负载')
    require(expected_ops in (40000,600000,2400000) or independent,'不是已登记的短测或正式规模')
    require(properties['recordcount']=='655360' and properties['operationcount']==str(expected_ops),'负载规模不符')
    if nowal:require(properties.get('rocksdb.disable_wal')=='true','不是已登记的无WAL配置')
    require(properties['rocksdb.fs_uri']=='zenfs://dev:nvme0n1' and properties['rocksdb.no_block_cache']=='true','未接入真实ZenFS或缓存配置不符')
    require('只读全键最新值哈希校验通过，键数=655360' in (root/'verify.log').read_text(),'最新值全键校验缺失')
    verify=(root/'zenfs.verify.log').read_text()
    require('RR 开始：' not in verify and 'GC 检查：' not in verify,'只读校验不应执行维护')
    text=(root/'zenfs.run.log').read_text();lines=text.splitlines()
    errors=[s for s in lines if '[ERROR]' in s or '状态=失败' in s]
    background_errors=[]
    if single_recovery:
        for log in sorted((root/'aux').rglob('LOG*')):
            if not log.is_file():continue
            for row in log.read_text(errors='replace').splitlines():
                if re.search(r'Zone allocation failure|Corruption:|Waiting after background (compaction|flush) error',row):
                    background_errors.append(dict(文件=str(log.relative_to(root)),原文=row))
    require(f'独立尾部约束：已启用，真实执行={int(mode!="greedy" and not direct_model)}，' in text,'策略入口未生效')
    require(f'成本排序：启用={int(mode=="cost")}，' in text,'成本排序入口不符')
    require(f'留存收益配置：采样=1，执行={int(mode=="retention")}，' in text,'留存入口不符')
    require(set(re.findall(r'GC 检查：.*启动水平=(\d+)%',text))=={'20'},'未确认原20%GC条件')
    if direct_model:
        from direct_analysis import parse_direct
        require('统一选区配置：启用=1，' in text and '独立约束决策：' not in text,'统一入口未启用或仍执行旧策略')
        model=RetentionLog(False)
        current_forecasts=[fields(s,'统一留存：') for s in lines if '统一留存：' in s]
        model.known=sum(r['可知']=='1' for r in current_forecasts)
        model.declining=sum(r['可知']=='1' and int(r['三十秒减少'])>0 for r in current_forecasts)
    elif benefit_budget:
        from benefit_log import BenefitLog
        require('收益预算配置：观察=1，执行=1，' in text,'收益预算入口未生效')
        model=BenefitLog(True)
    else:
        model=RetentionLog(mode=='retention')
    emergency=[];replay_lines=lines
    space_decisions=[]
    if space_emergency:
        from replay_space_emergency import replay as replay_space
        replay_lines,space_decisions=replay_space(replay_lines)
    if single_recovery:
        from replay_single_recovery import replay
        replay_lines,emergency=replay(replay_lines)
    # 维护失败仍保留可读诊断，但不放宽正式逐轮及迁移完成验收。
    decisions=[];maintenance=[];decision_error=None
    try:
        if direct_model:
            decisions,_,maintenance=parse_direct(replay_lines)
        elif '独立约束决策：' in text:
            decisions,_,maintenance=parse(replay_lines,'greedy' if mode=='greedy' else 'guarded',
                cost_rank=mode=='cost',proposal_model=model,
                budget_for=model.budget_for if benefit_budget else None)
        else:require('简单双因素候选：' not in text,'候选日志未闭合')
    except (ValueError,KeyError) as e:decision_error=str(e)
    zc=[(i,tuple(map(int,m.groups()))) for i,s in enumerate(lines) if (m:=ZC_STATS.search(s))]
    rr=[(i,tuple(map(int,m.groups()))) for i,s in enumerate(lines) if (m:=RR_STATS.search(s))]
    copied=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for _,r in zc+rr)
    ledger=(sum(int(c['有效字节']) for c in copied if c['复制成功']=='1')==moved and
        text.count('RR 开始：')==len(rr) and text.count('RR 完成：')==sum(r[2] for _,r in rr))
    require(ledger,'迁移复制或RR开始完成账目不符')
    require(all(r[2] in (0,1) for _,r in zc+rr),'非单区回收，配置或分析口径不符')
    ycsb=(root/'ycsb.log').read_text();timings=measurements(ycsb,expected_ops)
    if nowal:require(ycsb.count('实验WAL配置：关闭=1')==1,'未确认绑定实际禁用WAL')
    counts=dict(s.split('=',1) for s in (root/'audit.counts').read_text().splitlines())
    expected_counts={'装载':'0' if inherited_audit else '655360','读取':str(timings['READ']['次数']),'更新':str(timings['UPDATE']['次数']),'键数':'655360'}
    if inherited_audit:
        expected_counts['继承键数']='655360'
        require(sha(root/'audit.inherited.expected')==sha(root/'warmup/audit.expected'),'预热至热读校验清单未连续')
        require('Load runtime(sec):' not in ycsb,'继承阶段不应重新装载')
    require(counts==expected_counts,'审计计数与YCSB不符')
    # 上游状态时钟只有秒精度；阶段交界一秒内的维护单列，不伪装精确分阶段。
    load_end=0 if inherited_audit else ycsb.index('Load runtime(sec):')
    match=re.search(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) 0 sec:',ycsb[load_end:],re.M)
    require(match,'业务阶段起点未确认')
    begin=datetime.strptime(match.group(1),'%Y-%m-%d %H:%M:%S')
    def phase(number):
        stamp=datetime.strptime(lines[number][:26],'%Y/%m/%d-%H:%M:%S.%f')
        return '装载及等待' if stamp<begin else '业务' if stamp>=begin+timedelta(seconds=1) else '交界一秒不确定'
    phases={}
    for name in ('装载及等待','交界一秒不确定','业务'):
        z=[r for i,r in zc if phase(i)==name];r=[r for i,r in rr if phase(i)==name]
        phases[name]=dict(ZC尝试=len(z),ZC重置=sum(s[2] for s in z),RR尝试=len(r),RR重置=sum(s[2] for s in r),
            ZC迁移MiB=sum(s[1] for s in z)/MIB,RR迁移MiB=sum(s[1] for s in r)/MIB)
    complete=not errors and not background_errors and not decision_error and all(r[2]==1 for _,r in zc+rr)
    history_model=model.retention if benefit_budget else model
    return dict(验收通过=complete,模式=mode,软件清单SHA256=sha(root/'software.sha256'),
        无WAL实验=nowal,计划业务操作数=expected_ops,继承前阶段校验=inherited_audit,
        公共单区恢复=single_recovery,应急单区次数=sum(d['存在']=='1' for d in emergency),应急决策=emergency,
        空间紧急回收=space_emergency,空间紧急次数=len(space_decisions),
        空间紧急迁移MiB=sum(d['本轮迁移字节'] for d in space_decisions)/MIB,空间紧急决策=space_decisions,
        请求轨迹SHA256=sha(root/'audit.requests'),最新值清单SHA256=sha(root/'audit.expected'),
        ZC尝试=len(zc),ZC重置=sum(r[2] for _,r in zc),RR尝试=len(rr),RR重置=sum(r[2] for _,r in rr),
        ZC迁移MiB=sum(r[1] for _,r in zc)/MIB,RR迁移MiB=sum(r[1] for _,r in rr)/MIB,总迁移MiB=moved/MIB,
        调用延迟=timings,吞吐量=throughput(ycsb,expected_ops,properties),
        实际改选次数=sum(d['独立核验改选'] for d in decisions),
        留存历史可知候选次数=history_model.known,预测确有退役候选次数=history_model.declining,
        维护错误原文=errors,后台错误原文=background_errors,决策核验错误=decision_error,分阶段维护=phases,
        迁移口径='复制片段的逻辑字节，包含重复复制；不是NAND物理写入量。分阶段按维护完成日志时间归类，交界一秒单列，不推断跨界维护各阶段的实际复制比例。',
        决策=decisions,维护=maintenance,
        已验证='全键最新值哈希、请求计数、逐轮短期准入与尾部复核、留存评分及排序、完整复制账目；原始留存采样已归档'+('；独立重算收益预算、净成本及改选排序' if benefit_budget else ''),
        未确认='留存外推准确率、完整原始采样发布顺序重放、同一逻辑片段重复迁移量、长期稳态与统计显著性',
        边界='单次串行对照；相同请求与库不保证并发压实和物理布局相同；Update含Get再Put。平均值来自原始调用纳秒和，分位来自HdrHistogram；审计、请求生成和节流不在调用计时内，但影响墙钟吞吐。')

def compare(root):
    arms={m:json.loads((root/m/'独立核验.json').read_text()) for m in MODES}
    require(all(a['验收通过'] for a in arms.values()),'存在无效组')
    for key in ('软件清单SHA256','请求轨迹SHA256','最新值清单SHA256'):
        require(len({a[key] for a in arms.values()})==1,'四组身份不同：'+key)
    require(arms['greedy']['实际改选次数']==0,'Greedy发生改选')
    keys=('ZC尝试','RR重置','总迁移MiB','ZC迁移MiB','RR迁移MiB')
    delta={m:{k:arms[m][k]-arms['greedy'][k] for k in keys} for m in MODES[1:]}
    return dict(配对核验通过=True,各组=arms,相对Greedy=delta,
        结论='是否有效以总迁移、实际改选、RR和延迟共同判断；没有实际改选时，不把组间波动归因于策略。',
        额外消融='cost与retention差异用于区分低成本重排和新增留存趋势；单次结果仍不构成因果或泛化证明。')
