"""仅核验条带试验的失败前缀；不放宽成功分析器，不将现场改写为成功。"""
import csv,hashlib,io,json
from collections import defaultdict
from pathlib import Path
from analyze_pair_rate import pair_audit
from analyze_rr_opportunities import maintenance_audit
from analyze_maintenance_overlap import RR_STATS,RR_START
from analyze_v1_shadow_stream import fields,require

LABEL='rocksdb-anchor-20261016'
SECOND=10**9

def complete_rows(path):
    data=path.read_text();end=data.rfind('\n')+1
    return list(csv.DictReader(io.StringIO(data[:end]))),len(data)-end

def threshold_outcome(block,observations):
    """只追踪原物理块；换最大块、Reset或查询不稳时不跨越后继续寻找。"""
    start=int(block['快照纳秒']);last=start;rc=int(block['当前RC'])
    session=block['设备会话'].split('/')
    for row in observations:
        now=int(row['设备快照纳秒'])
        if now<=start:continue
        if ([row['会话高位'],row['会话低位']]!=session or row['设备代际后']!=block['设备代际']):
            return dict(阈值可核实=False,原因='会话或设备Reset代际改变')
        if row['统计稳定']!='1' or row['块编号']!=block['块编号'] or int(row['RC'])<rc:
            return dict(阈值可核实=False,原因='原最大块变化、查询不稳或RC倒退')
        if int(row['RC'])>=4096:
            return dict(阈值可核实=True,确认未达阈值下界秒=(last-start)/SECOND,
                确认已达阈值上界秒=(now-start)/SECOND,确认RC=int(row['RC']),
                说明='相邻观测夹逼，不将首次观察超阈值时刻当精确越阈时刻')
        last=now;rc=int(row['RC'])
    return dict(阈值可核实=False,原因='前缀结束前未确认原块达阈值')

def audit(root):
    require(root.name==LABEL and not (root/'独立核验.json').exists(),'不是本轮未完成试验')
    run=root/'运行归档'/LABEL;wrapper=root/'运行归档'/f'rrzc-{LABEL}-tests'
    require(not (run/'run.exit').exists() and not (run/'verify.exit').exists(),'原业务已结束，不能按此未完成前缀分析')
    require((wrapper/'build.exit').read_text().strip()=='0' and (wrapper/'selftest.exit').read_text().strip()=='0','构建或最小回归未通过')
    require((root/'生产身份.before.sha256').read_bytes()==(root/'生产身份.after.sha256').read_bytes(),'生产库身份变化')
    expected={n:d for d,n in (s.split() for s in (root/'生产身份.before.sha256').read_text().splitlines())}
    expected.update({n:d for d,n in (s.split() for s in (root/'测试身份.sha256').read_text().splitlines())})
    initial={n:d for d,n in (s.split() for s in (run/'identity.initial.sha256').read_text().splitlines())}
    require(all(initial.get(n)==d for n,d in expected.items()),'实际库或测试身份不符')
    require(json.loads((root/'运行归档/归档状态.json').read_text())['完整'],'传输有缺失')
    db_log=root/'运行归档'/f'{LABEL}-aux/rrzc-real/LOG'
    db_lines=db_log.read_text().splitlines()
    failures=[dict(行号=i,原文=s) for i,s in enumerate(db_lines,1) if 'Zone allocation failure' in s]
    stops=[dict(行号=i,原文=s) for i,s in enumerate(db_lines,1) if 'Stopping writes because we have 4 immutable memtables' in s]
    require(failures and stops,'缺分配失败或停止写入的原始证据')
    error_stamp=failures[0]['原文'][:26]
    lines=(run/'zenfs.run.log').read_text().splitlines()
    paired=pair_audit(lines);maintenance=maintenance_audit(lines)
    c2h={}
    for s in lines:
        if 'V1影子候选：' in s:
            b=fields(s,'V1影子候选：');c2h[b['轮次'],b['Zone']]=b
    observations,tail=complete_rows(run/'zones.csv');by_zone=defaultdict(list)
    for r in observations:by_zone[r['Zone']].append(r)
    copies={};current=None
    for i,s in enumerate(lines,1):
        if (m:=RR_START.search(s)):current=(i,m.group(1),s[:26])
        if (m:=RR_STATS.search(s)):
            require(current is not None,'RR统计缺开始')
            copies[current[0]]=dict(Zone=current[1],实际迁移字节=int(m.group(2)),成功重置=bool(int(m.group(3))),
                开始时钟=current[2],在首次分配失败之后=current[2]>error_stamp)
            current=None
    eligible=[o for o in paired['候选观察'] if o['合格']];results=[]
    for o in eligible:
        if not o['双点证据']['界限内']:continue
        greedy=next(g for g in eligible if g['轮次']==o['轮次'] and g['是Greedy'])
        delta=max(0,o['有效字节']-greedy['有效字节'])
        future=[]
        for reuse in maintenance['同地址后续RR核对']:
            c=reuse['候选'];r=reuse['后续RR']
            if str(c['轮次'])==o['轮次'] and c['Zone']==o['Zone'] and reuse['同一主机代际']:
                future.append(dict(RR开始=r,复制统计=copies.get(r['行号'])))
        results.append(dict(轮次=o['轮次'],Zone=o['Zone'],有效字节=o['有效字节'],Greedy有效字节=greedy['有效字节'],
            额外有效字节=delta,超过原16MiB预算=delta>16<<20,预计达阈值秒=o['双点证据']['预测达阈值纳秒']/SECOND,
            同块阈值观察=threshold_outcome(c2h[o['轮次'],o['Zone']],by_zone[o['Zone']]),同主机代际后续RR=future))
    return dict(完整实验成功=False,独立数据校验完成=False,性能基线可用=False,
        生产库与测试身份核对通过=True,构建及普通文件系统回归通过=True,
        分配失败原文=failures,写入停止原文=stops,双点候选前缀=paired,紧迫候选后续=results,
        前缀未完整末行字符数=dict(zones_csv=tail),
        输入副本SHA256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (run/'zenfs.run.log',run/'zones.csv',db_log)},
        归档边界='运行中非原子副本，传输完成不等于原业务完成；没有run.exit或verify.exit，不补造成功。宿主等待已停止，业务及客机保留。',
        结论边界='候选确有风险但被额外搬运预算挡住；后续RR发生在写入出错条件下，不能作为正常稳态维护成本、改选收益或反事实证明。')

def timeline_audit(root):
    original=audit(root)
    require(original==json.loads((root/'失败前缀核验.json').read_text()),'原前缀核验复算不一致')
    lines=(root/'运行归档'/LABEL/'zenfs.run.log').read_text().splitlines()
    stamps={}
    for s in lines:
        if 'V1影子候选：' in s:
            b=fields(s,'V1影子候选：');stamps[b['轮次'],b['Zone']]=s[:26]
    first_error=original['分配失败原文'][0]['原文'][:26]
    before=[];after=[]
    for r in original['紧迫候选后续']:
        item=dict(r,候选日志时钟=stamps[r['轮次'],r['Zone']])
        item['在首次分配失败前']=item['候选日志时钟']<first_error
        (before if item['在首次分配失败前'] else after).append(item)
    return dict(首次已记录分配失败时钟=first_error,失败前紧迫候选数=len(before),失败后紧迫候选数=len(after),
        失败前候选=before,失败后候选=after,完整实验成功=False,
        时钟边界='先后比较只用同客机两种日志的墙钟；阈值夹逼只用设备单调时间，两者不直接相减。',
        成本边界='相对Greedy多搬的源有效字节不等于最终净成本；还缺空间回收差、尾部与重复搬运、额外ZC及完整业务对照。不能直接用后续RR字节减额外源字节宣称收益。')

if __name__=='__main__':
    import sys
    require(len(sys.argv) in (2,3) and (len(sys.argv)==2 or sys.argv[2]=='--timeline'),'需要本轮归档根目录，可选--timeline')
    root=Path(sys.argv[1]).resolve();timeline=len(sys.argv)==3
    output=root/('失败时序复核.json' if timeline else '失败前缀核验.json');require(not output.exists(),'输出已存在，不覆盖')
    output.write_text(json.dumps(timeline_audit(root) if timeline else audit(root),ensure_ascii=False,indent=2)+'\n')
    print('失败前缀已核验；未把未完成业务改写为成功。')
