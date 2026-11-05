"""期望RR的YCSB显式验收，保留旧窗口入口和原始结果。"""
from collections import Counter
import argparse
import json
from pathlib import Path

from analyze_window_ycsb import analyze_arm as shared_arm
from analyze_joint_opportunity import require,fields,model_decisions
from analyze_window_ycsb import digest


def analyze_arm(root,stage,mode,*,strict_rr=False):
    root=Path(root);out=shared_arm(root,stage,mode,expected_rr=True,strict_rr=strict_rr)
    lines=(root/'zenfs.run.log').read_text().splitlines();changes=[]
    blocks={}
    for line in lines:
        if 'V1影子候选：' in line:
            b=fields(line,'V1影子候选：');blocks[b['轮次'],b['Zone']]=b
    for item in model_decisions(lines):
        safe=[r for r in item['成本'] if r['安全']=='1']
        if not safe or item['决策']['实际存在']!='1':continue
        greedy=min(safe,key=lambda r:(int(r['有效']),int(r['Zone'])))
        selected=next(r for r in safe if r['Zone']==item['决策']['实际Zone'])
        if greedy['Zone']!=selected['Zone']:
            block=blocks[item['决策']['轮次'],selected['Zone']]
            changes.append(dict(轮次=int(item['决策']['轮次']),实际Zone=int(selected['Zone']),
                同轮Greedy=int(greedy['Zone']),当前额外有效字节=int(selected['有效'])-int(greedy['有效']),
                预计抵扣字节=int(selected['RR抵扣']),ETA纳秒=int(selected['ETA']),
                最大块RC=int(block['当前RC']),已达RR阈值=int(block['当前RC'])>=1024))
    final=json.loads((root/'zones.final.json').read_text())['zone_list']
    checks=[dict(日志时点=line.split()[0],**fields(line,'GC 检查：')) for line in lines if 'GC 检查：' in line]
    out.update(相对同轮Greedy改选=changes,最终硬件Zone状态=dict(Counter(z['state'] for z in final)),
        最近GC空间采样=checks[-1] if checks else None,
        末态口径='硬件Zone状态在业务及只读校验后取得；最近GC空间采样非结束瞬间快照，不代表精确有效数据量',
        模型='期望RR实验V1；泊松假设未校准，直接净复制/回收排序；未改变RR/GC触发条件')
    if strict_rr:
        before=json.loads((root/'strict.business.json').read_text())
        after=json.loads((root/'strict.after-verify.json').read_text())
        for r in (before,after):
            require(r['版本']==r['启用']==1 and 0<=r['历史最高RC']<=1024,'严格RR未启用或计数越界')
            require(r['实际NAND读取']==r['计入RC读取']+r['自身刷新豁免读取'],'严格读取账目不闭合')
            require(r['其他块搬迁计数读取']<=r['计入RC读取'],'其他块迁移没有正常计数')
        require(all(after[k]>=before[k] for k in before),'校验阶段严格账目回退')
        if stage=='short':
            require(before['阈值触发事件']>0 and out['RR次数']>0 and before['历史最高RC']==1024,
                '短测没有覆盖真实严格RR触发，不能验收为RR研究对照')
            require(before['自身刷新豁免读取']>0 and before['其他块搬迁计数读取']>0,
                '短测没有同时覆盖自身豁免及其他块正常计数')
        out.update(严格RR业务账目=before,严格RR校验后账目=after,
            末态口径='zones.final及设备代际均在业务完成、独立校验之前采集；校验可能单独执行RR，不混入业务指标',
            模型='原期望RR选区核未改；双方统一严格计数及同步维护；不能与旧十秒RR实验视作相同系统')
    return out


def compare_pair(root,stage,*,strict_rr=False):
    root=Path(root);arms={}
    for mode in ('greedy','expected'):
        out=root/(stage+'-'+mode)
        state=json.loads((out/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'归档或客机退出不完整')
        if strict_rr:arms[mode]=analyze_arm(out/(stage+'-'+mode),stage,mode,strict_rr=True)
        else:arms[mode]=analyze_arm(out/(stage+'-'+mode),stage,mode)
    a,b=arms['greedy'],arms['expected']
    require(a['请求SHA256']==b['请求SHA256'] and a['最终值SHA256']==b['最终值SHA256'],'请求或最终数据不同')
    folders=[root/(stage+'-'+mode)/(stage+'-'+mode) for mode in ('greedy','expected')]
    for name in ('software.before.log','workload.properties'):
        require((folders[0]/name).read_bytes()==(folders[1]/name).read_bytes(),'配对条件不同：'+name)
    return dict(配对验收通过=True,阶段=stage,模式=arms,
        模型相对Greedy差值={key:b[key]-a[key] for key in
            ('RR次数','ZC次数','总迁移字节','总成功ZoneReset','推导物理Block擦除次数')},
        边界='同库同负载同请求、各臂新NAND；不是相同内存快照；单轮不证明稳定收益，须同时检查末态空间')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--stage',choices=('smoke','short'),required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖原实验或已有核验')
    result=compare_pair(args.root,args.stage)
    result.update(重新核验说明='只修正已达阈值时未使用增长诊断的等同假设；保留原监督失败及冻结分析源码，不改设备、评分和原始数据',
        原监督状态=json.loads((args.root/'状态.json').read_text()),
        分析器身份={p.name:digest(p) for p in (Path(__file__),Path(__file__).with_name('analyze_window_credit.py'),
            Path(__file__).with_name('analyze_window_ycsb.py'),Path(__file__).with_name('replay_expected_rr.py'),
            Path(__file__).with_name('replay_persistent_credit.py'))})
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(配对验收通过=result['配对验收通过'],差值=result['模型相对Greedy差值']),ensure_ascii=False))


if __name__=='__main__':main()
