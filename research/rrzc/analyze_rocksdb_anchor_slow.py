"""完整限速短测的机会分类：区分阈值前预测、已达阈值与重复建议。"""
import hashlib,json
from collections import Counter
from pathlib import Path
from analyze_rocksdb_anchor import audit as base_audit
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require

LABEL='rocksdb-anchor-slow-20261016'

def category(candidate):
    evidence=candidate['双点证据']
    if not evidence['增量可知']:return '未知'
    if candidate['当前RC']>=4096:return '当前已经达阈值'
    if evidence['界限内']:return '阈值前预测近期达阈值'
    return '阈值前未预测紧迫'

def summarize(paired,blocks):
    eligible=[c for c in paired['候选观察'] if c['合格']]
    selected=[]
    for d in paired['影子建议']:
        if d['会改选']!='1':continue
        matches=[c for c in eligible if c['轮次']==d['轮次'] and c['Zone']==d['建议Zone']]
        require(len(matches)==1,'建议缺唯一合格候选')
        c=matches[0];b=blocks[c['轮次'],c['Zone']]
        selected.append(dict(轮次=c['轮次'],Zone=c['Zone'],设备会话=b['设备会话'],设备代际=b['设备代际'],
            块编号=b['块编号'],当前RC=c['当前RC'],真实增量=c['双点证据']['真实增量'],
            分类=category(c),有效字节=c['有效字节'],额外有效字节=int(d['额外有效字节']),
            预测达阈值纳秒=c['双点证据']['预测达阈值纳秒']))
    return dict(合格候选重复观察数=len(eligible),合格候选分类=dict(Counter(category(c) for c in eligible)),
        影子改选建议次数=len(selected),建议分类=dict(Counter(c['分类'] for c in selected)),
        建议去重区代际数=len({(c['Zone'],c['设备会话'],c['设备代际']) for c in selected}),
        逐次建议=selected,
        统计边界='建议次数不是独立区数；已达阈值ETA为零不是读速率外推命中。零近期增量不代表无RR待处理。')

def audit(root):
    require(root.name==LABEL,'不是本轮固定限速归档')
    saved=json.loads((root/'独立核验.json').read_text())
    require(base_audit(root,slow=True)==saved,'完整核验复算不一致')
    require(saved['维护无错误'],'维护有错误，不能作为无错误完整短测')
    run=root/'运行归档'/LABEL;lines=(run/'zenfs.run.log').read_text().splitlines();blocks={}
    for s in lines:
        if 'V1影子候选：' in s:
            b=fields(s,'V1影子候选：');blocks[b['轮次'],b['Zone']]=b
    result=summarize(saved['双点速率核验'],blocks)
    snapshots=rows(run/'zones.csv');mapping=rows(run/'files.csv')
    for c in result['逐次建议']:
        observed=any(str(r['候选']['轮次'])==c['轮次'] and r['候选']['Zone']==c['Zone'] and r['同一主机代际']
            for r in saved['维护事实']['同地址后续RR核对'])
        end=[r for r in snapshots if r['Zone']==c['Zone']][-1]
        same=(end['会话高位']+'/'+end['会话低位']==c['设备会话'] and
              end['设备代际后']==c['设备代际'] and end['块编号']==c['块编号'] and end['统计稳定']=='1')
        c.update(观察内同主机代际后续RR=observed,末次快照同会话代际原块=same,
            末次RC=int(end['RC']) if same else None,末次有效字节=int(end['有效字节']),
            末次映射=[dict(文件=m['文件'],区内字节=int(m['区内有效字节'])) for m in mapping
                if m['批次']==end['批次'] and m['Zone']==c['Zone']],
            后续边界='观察内未见RR不等于以后不会RR，也不证明改选有收益；停止业务后的未知时间不补成负标签。')
    result.update(完整核验逐字段一致=True,实际执行新机制=False,
        完成请求=dict(读=saved['读请求数'],写键=saved['覆盖写键数']),
        维护=dict(ZC=saved['ZC'],RR=saved['RR成功'],总迁移MiB=saved['总迁移MiB']),
        输入身份={n:hashlib.sha256((run/n).read_bytes()).hexdigest() for n in ('zenfs.run.log','zones.csv','files.csv')},
        完整核验SHA256=hashlib.sha256((root/'独立核验.json').read_bytes()).hexdigest(),
        分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        结论边界='只确认该固定负载可完整运行；阈值前预测与已超阈值待处理必须分开。未调整预算、机制或负载，不是稳态或策略收益对照。')
    return result

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要本轮归档根目录');root=Path(sys.argv[1]).resolve()
    output=root/'机会分类复核.json';require(not output.exists(),'输出已存在，拒绝覆盖')
    output.write_text(json.dumps(audit(root),ensure_ascii=False,indent=2)+'\n')
    print('完整限速短测及机会分类复算通过，未执行新机制。')
