"""只读核验相邻范围先导及旧负载，定位GC入口并补齐吞吐量，不重跑实验。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
from analyze_ycsb_retention import audit,sha
from analyze_v1_shadow_stream import require


def gc_checks(text):
    rows=[]
    pattern=re.compile(r'GC 检查：空闲=(\d+) 字节，非空闲=(\d+) 字节，空闲比例=(\d+)%，启动水平=(\d+)%，进入选区=([01])')
    for number,line in enumerate(text.splitlines(),1):
        if 'GC 检查：' not in line:continue
        match=pattern.search(line)
        require(match is not None,'GC检查字段不完整')
        free,nonfree,percent,level,entered=map(int,match.groups())
        require(free+nonfree>0 and percent==100*free//(free+nonfree),'GC比例与字节数不一致')
        require(entered==int(percent<=level),'GC分支与启动线不一致')
        rows.append(dict(行号=number,时间=line[:26],空闲字节=free,非空闲字节=nonfree,
            空闲百分比=percent,启动百分比=level,进入选区=entered))
    require(rows,'缺GC检查，不能断言未触发原因')
    return dict(检查次数=len(rows),进入选区次数=sum(r['进入选区'] for r in rows),
        空闲百分比最小=min(r['空闲百分比'] for r in rows),
        空闲百分比最大=max(r['空闲百分比'] for r in rows),
        启动线=sorted({r['启动百分比'] for r in rows}),
        最低空闲观测=min(rows,key=lambda r:r['空闲百分比']),
        末十二次空闲百分比=[r['空闲百分比'] for r in rows[-12:]],
        边界='仅代表约每10秒检查时的观测，不证明两个检查点之间从未低于启动线；空闲口径包括IO Zone剩余可写容量。')


def analyze(root,mode,locality=False):
    root=Path(root)
    result=audit(root,mode,nowal=True,expected_ops=1200000,single_recovery=True,
        space_emergency=True,independent_ratio='50/50',update_range=(4096,61440) if locality else None)
    require(result['验收通过'],'原始数据或维护核验不通过')
    text=(root/'zenfs.run.log').read_text()
    resets=Counter(int(m[1]) for m in re.finditer(r'Zone 重置诊断：Zone=(\d+)，状态=成功',text))
    logs=sorted(p for p in (root/'aux').rglob('LOG*') if p.is_file())
    compactions=[]
    for path in logs:
        for line in path.read_text(errors='replace').splitlines():
            if 'EVENT_LOG_v1 {' not in line:continue
            row=json.loads(line.split('EVENT_LOG_v1 ',1)[1])
            if row.get('event')=='compaction_finished':compactions.append(row)
    return dict(原始目录=str(root),原始证据SHA256={str(p.relative_to(root)):sha(p)
        for p in [root/'zenfs.run.log',root/'ycsb.log',root/'workload.properties',*logs]},
        验收通过=result['验收通过'],GC入口=gc_checks(text),
        维护={k:result[k] for k in ('ZC重置','RR重置','总迁移MiB','分阶段维护')},
        吞吐量=result['吞吐量'],调用延迟=result['调用延迟'],
        全日志Reset成功次数=sum(resets.values()),按Zone重置次数=dict(sorted(resets.items())),
        SST退役日志条数=len(re.findall(r'V1后续文件退役：.*\.sst，',text)),
        RocksDB压实=dict(完成事件数=len(compactions),输出MiB=sum(r['total_output_size'] for r in compactions)/(1<<20),
            边界='全日志含装载和收尾的压实事件输出，非ZC/RR搬运，也非NAND物理写入；不直接与维护量混为一个口径。'),
        解释边界='Reset含元数据、文件失效后的空区回收、RR等来源，不等于ZC次数；本报告不凭计数差为每次Reset猜测原因。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,default=Path('/home/data/xwz/rrzc-baselines'))
    args=parser.parse_args()
    rows={}
    for label,name,mode,locality in (
        ('相邻范围Greedy','ycsb-locality-20261019-results','greedy',True),
        ('全库更新Greedy','ycsb-balanced-pair-20261019-results','greedy',False),
        ('全库更新留存','ycsb-balanced-pair-20261019-results','retention',False)):
        rows[label]=analyze(args.base/name/mode/'20261017',mode,locality)
    print(json.dumps(dict(实验=rows,
        已确认='相邻范围先导是否进入ZC由GC入口字段直接判定；正常GC触发在选择模型之前。',
        推断='更新范围集中后，压实局限性及整区失效回收改变了空间压力，可能使GC入口机会减少；不声称已隔离各路径的因果贡献。',
        禁止结论='负载不同不能把吞吐量或迁移差异称为模型收益；这轮无正常选区时不能验证V2。',
        下一步建议='保留热读键和原GC/RR条件，只把相邻更新区间扩大到约1GiB逻辑范围，先做一次Greedy机会先导；不是已验证的最佳参数，不自动启动长测。'),
        ensure_ascii=False,indent=2))


if __name__=='__main__':main()
