"""只读核验近期ETA功能配对；预测精度、文件时间可用性与维护收益分开。"""
import argparse
import json
from pathlib import Path

from analyze_joint_opportunity import audit, compare, fields, require

MAX=(1 << 64)-1
SECOND=10**9


def ceil_div(a,b):return (a+b-1)//b


def validate_rate(r, block=None):
    """关联同次C2快照的有效位；非完整窗口不能因有非零增量而外推。"""
    require(block is not None, '缺同块窗口标志，不能假定十秒统计完整')
    pairs=(('轮次','轮次'),('Zone','Zone'),('设备会话','会话'),('设备代际','代际'),
           ('块编号','块'),('PPA','PPA'),('快照纳秒','快照'),('代际起点纳秒','起点'),
           ('当前RC','RC'),('十秒真实增量','十秒增量'))
    require(all(block.get(a)==r.get(b) and a in block and b in r for a,b in pairs),
            'ETA与候选不是同会话/代际/物理块/快照')
    current,threshold=int(r['RC']),int(r['阈值'])
    flags=int(block['窗口标志']);age=int(r['快照'])-int(r['起点']);delta10=int(r['十秒增量'])
    require(0<threshold<=MAX and 0<=delta10<=current<=MAX and 0<=flags<=15 and age>=0,
            'ETA参数或窗口标志不合法')
    require(not(flags&2) or age>=10*SECOND,'十秒完整标志与Reset年龄矛盾')
    require(not(flags&4) or (flags&2 and age>=30*SECOND and not(flags&8)),
            '三十秒完整标志矛盾')
    enabled=bool(flags&1) and not(flags&8)
    old=MAX
    if enabled:
        if current>=threshold:old=0
        elif flags&2 and delta10:old=min(MAX,ceil_div((threshold-current)*10*SECOND,delta10))
    require(int(r['原ETA'])==old,'原十秒ETA与真实窗口不一致')
    require(r['近期可知'] in ('0','1'),'近期可知标志不合法')
    if old==MAX:require(int(r['原抵扣'])==0,'十秒ETA未知或无增长却获得RR抵扣')
    if not enabled:
        require(int(r['新ETA'])==MAX and r['近期可知']=='0' and
                int(r['原抵扣'])==0 and int(r['新抵扣'])==0,'统计失效仍输出ETA或RR抵扣')
    elif r['近期可知']=='1':
        require(all(r[a]==r[b] for a,b in (('会话','前会话'),('代际','前代际'),('块','前块'),('PPA','前PPA'),('起点','前起点'))),
                '不允许跨会话/Reset/最大块变化拼接近期读增量')
        delta,span=int(r['近期增量']),int(r['间隔'])
        require(SECOND//2<=span<=2*SECOND and int(r['快照'])-int(r['前快照'])==span and
                current-int(r['前RC'])==delta>=0,'近期同块计数或时间不一致')
        revised=0 if current>=threshold else min(MAX,ceil_div((threshold-current)*span,delta)) if delta else MAX
        require(int(r['新ETA'])==revised,'新ETA没有使用已验证的同块短间隔速率')
    elif current<threshold:
        require(int(r['新ETA'])==MAX and int(r['新抵扣'])==0,'未知近期速率却借用旧窗口给予抵扣')
    else:
        require(int(r['新ETA'])==0,'已达阈值却没有报告零ETA')
    return dict(轮次=int(r['轮次']),Zone=int(r['Zone']),原ETA秒=int(r['原ETA'])/SECOND if int(r['原ETA'])<MAX else None,
        新ETA秒=int(r['新ETA'])/SECOND if int(r['新ETA'])<MAX else None,
        近期读每秒=int(r['近期增量'])*SECOND/int(r['间隔']) if r['近期可知']=='1' else None,
        当前RC=current,阈值=threshold,窗口标志=flags,十秒窗口完整=bool(flags&2),
        前块身份核验通过=r['近期可知']=='1')


def analyze_arm(root,mode,scenario):
    # 两臂均走直接成本内核，只有ETA分支不同；不是Greedy对照。
    actual=audit(root,'direct',scenario);actual['模式']=mode
    lines=(root/'zenfs.run.log').read_text().splitlines()
    rows=[fields(line,'近期ETA对照：') for line in lines if '近期ETA对照：' in line]
    require(rows and all(r['新策略']==str(int(mode=='recent')) for r in rows),'执行开关未按本轮模式生效')
    blocks={}
    for line in lines:
        if 'V1影子候选：' in line:
            b=fields(line,'V1影子候选：');blocks[b['轮次'],b['Zone']]=b
    result=[validate_rate(r,blocks.get((r['轮次'],r['Zone']))) for r in rows]
    costs={}
    for line in lines:
        if '统一成本：' in line:
            r=fields(line,'统一成本：');costs[r['轮次'],r['Zone']]=r
    for r in rows:
        c=costs[r['轮次'],r['Zone']];prefix='新' if mode=='recent' else '原'
        require(c['ETA']==r[prefix+'ETA'] and c['RR抵扣']==r[prefix+'抵扣'],
                '实际选区仍使用了错误ETA或抵扣分支')
    signals=[fields(line,'文件时间信号：') for line in lines if '文件时间信号：' in line]
    require(signals and all(r['未来失效时刻未知']=='1' for r in signals),'文件时间信号缺失或混入未来寿命')
    for r in signals:
        known=int(r['修改秒'])>0 and int(r['观测秒'])>=int(r['修改秒'])
        require(r['修改龄可知']==str(int(known)) and
                int(r['修改龄秒'])==(int(r['观测秒'])-int(r['修改秒']) if known else 0),
                '修改龄不是实际观测时间差')
    return dict(维护验收=actual,ETA逐轮核验=result,文件时间信号=signals,
        边界='修改龄非出生年龄或剩余寿命，寿命提示不参与本轮评分；未用未来删除信息')


def analyze(root):
    cases={}
    for scenario in ('opportunity','retire'):
        arms={mode:analyze_arm(root/f'{scenario}-{mode}'/scenario,mode,scenario) for mode in ('direct','recent')}
        paired=compare(arms['direct']['维护验收'],arms['recent']['维护验收'])
        cases[scenario]=dict(配对验收通过=paired['配对验收通过'],模式=arms,
            近期ETA相对原版RR减少=paired['窗口内RR减少'],近期ETA相对原版复制减少字节=paired['窗口内复制减少字节'])
    return dict(核验通过=all(c['配对验收通过'] for c in cases.values()),场景=cases,
        口径='新旧ETA真实同库受控对照，非YCSB；更准确的ETA不等于减少RR或复制，不声称预测突然失效')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖已有核验')
    result=analyze(args.root)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(核验通过=result['核验通过'],场景={k:{name:v[name] for name in (
        '配对验收通过','近期ETA相对原版RR减少','近期ETA相对原版复制减少字节')} for k,v in result['场景'].items()}),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
