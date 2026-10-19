"""固定条带锚点的一次机会覆盖验证；不比较策略性能，不自动重试。"""
import json
from analyze_rocksdb_observe import analyze,factor_summary
from analyze_pair_rate import pair_audit
from analyze_rocksdb_recent import sampling_audit
from analyze_rocksdb_pressure import gc_audit
from analyze_rr_opportunities import snapshot_audit,maintenance_audit
from analyze_stream import rows
from analyze_v1_shadow_stream import require

LABEL='rocksdb-anchor-20261016'

def audit(root,slow=False):
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'运行期间生产库变化')
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    run=root/'运行归档'/('rocksdb-anchor-slow-20261016' if slow else LABEL)
    result=analyze(run,expected,anchor=not slow,anchor_slow=slow)
    lines=(run/'zenfs.run.log').read_text().splitlines()
    require(any('双点速率配置：启用=1' in s for s in lines),'双点入口未生效')
    result['双点速率核验']=pair_audit(lines)
    result['原增长与后台采样']=sampling_audit(lines)
    result['原GC候选覆盖']=gc_audit(lines)
    result['全阶段机会观测']=snapshot_audit(rows(run/'zones.csv'),rows(run/'files.csv'))
    result['维护事实']=maintenance_audit(lines)
    result['事前信号汇总']=factor_summary(result)
    pair=result['双点速率核验']
    result['预登记机会验收']=dict(出现紧迫合格候选=pair['双点紧迫候选数']>0,
        出现预算内影子改选=pair['影子建议改选数']>0,实际执行新机制=False,
        结论边界='固定目标负载，逻辑条带不保证物理同区；只有实际候选日志才证明准入机会。没有建议也保留，不据此改变阈值或自动重跑。')
    return result

if __name__=='__main__':
    import sys
    from pathlib import Path
    require(len(sys.argv)==2,'需要本轮归档目录');root=Path(sys.argv[1]).resolve()
    require(root.name==LABEL and not (root/'独立核验.json').exists(),'目录不符或输出已存在')
    (root/'独立核验.json').write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
