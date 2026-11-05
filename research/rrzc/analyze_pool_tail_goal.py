"""核算真实业务后共同空间下限的追加成本，保留整Zone回收的超额空间。"""
import argparse
import csv
import json
from pathlib import Path
import shutil
import tempfile

from audit_colocation_transactions import audit
from analyze_reproducibility import accounting
from ycsb_af import digest,require
from analyze_all_blocks_dbbench import fields

GOAL=1530117632
COUNTERS=('实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取','阈值触发事件','被阻止读取')


def load(path):return json.loads(path.read_text())


def goal_arm(path,business):
    rows=[json.loads(l) for l in (path/'goal.log').read_text().splitlines() if l.startswith('{')]
    require(rows and [r['序号'] for r in rows]==list(range(len(rows))),'收尾检查序号异常')
    require(all(r['目标字节']==GOAL and r['有效字节']==business['末端空间']['有效字节'] for r in rows),
        '收尾目标或有效量变化')
    require(all(not r['达标'] for r in rows[:-1]),'已达标后仍继续回收')
    log=(path/'goal.zenfs.log').read_text()
    require('[ERROR]' not in log and '原启动水平=20%' in log and '主机端 RR=1' in log,
        '收尾出错或未沿用原维护条件')
    require('严格RR主机配置：启用=1' in log,'收尾严格RR缺失')
    with tempfile.TemporaryDirectory(prefix='rrzc-goal-ledger-') as temp:
        p=Path(temp);(p/'zenfs.log').write_text('受控放置业务开始：派生结算窗\n'+log+'\n受控放置业务截止：派生结算窗\n')
        tx=audit(p)
    ledger=accounting(path,'goal.zenfs.log');require(tx['维护账目']==ledger,'结算逐笔账目不一致')
    require(all('模式=valid，' in t['原始选择行'] for t in tx['事务'] if t['类型']=='ZC'),
        '收尾没有使用原Greedy')
    final=load(path/'space.goal.json');old=load(path/'strict.business.json');new=load(path/'strict.goal.json')
    require(final['有效字节']==business['末端空间']['有效字节'],'收尾改变有效量')
    require(final['空闲字节']>=rows[-1]['空闲字节'],'关闭清账后空闲空间反而下降')
    require(new['历史最高RC']<=1024,'收尾越过RR安全阈值')
    delta={k:new[k]-old[k] for k in COUNTERS};require(min(delta.values())>=0,'设备累计计数倒退')
    return dict(业务=business['维护'],收尾=ledger,收尾事务=tx['事务'],目标字节=GOAL,
        目标达成=final['空闲字节']>=GOAL,收尾检查数=len(rows)-1,逐次空间=rows,
        业务末端空闲字节=business['末端空间']['空闲字节'],最终空闲字节=final['空闲字节'],
        超额空间字节=max(0,final['空闲字节']-GOAL),最终有效字节=final['有效字节'],
        业务加收尾迁移字节=business['维护']['总迁移字节']+ledger['总迁移字节'],
        业务加收尾Reset=business['维护']['全部成功Reset次数']+ledger['全部成功Reset次数'],
        收尾设备增量=delta,日志SHA256=digest(path/'goal.zenfs.log'),
        边界='挂载、原维护和正常关闭均入账；只读探针的设备读取差额也保留。无业务API统计。在线H热度在重挂载后重新学习，物理RC和W来源标签保留。')


def analyze(root,output):
    state=load(root/'状态.json');stage=load(root/'阶段归档.json')
    require(state.get('完整实验完成') and state['当前客机PID'] is None and stage['完整'] and stage['业务成功'],
        '尚未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    require(not output.exists(),'不覆盖已有空间结算报告')
    reports={}
    for tag in ('p1','m1'):
        require(load(root/(tag+'-原业务复现核验.json'))['通过'],'原业务未复现')
        reports[tag]=goal_arm(root/'逐臂采证'/tag,load(root/(tag+'-核验.json')))
        require(reports[tag]==load(root/(tag+'-空间结算核验.json')),'归档后重算不同')
    a,b=reports['p1'],reports['m1'];delta=b['业务加收尾迁移字节']-a['业务加收尾迁移字节']
    if not all(r['目标达成'] for r in reports.values()):
        conclusion='原规则未使两组都达到预定下限，不能发布等空间净收益。'
    elif delta<0 and b['最终空闲字节']>=a['最终空闲字节']:
        conclusion='本次实际结算后总复制更少，且开启组最终可用空间不低；仍仅是一对受控路径。'
    elif delta>=0:
        conclusion='补足共同空间下限后，开启组总复制没有减少；业务窗口内的改善未兑现为本次结算的复制净收益。'
    else:
        conclusion='两组达到共同下限，但开启组最终空闲更少；仍须同时评价超额空间差，不能称完全相同空间。'
    output.mkdir(parents=True)
    evidence={}
    for tag in reports:
        log=(root/'逐臂采证'/tag/'goal.zenfs.log').read_text()
        evidence[tag]={marker:[fields(l,marker) for l in log.splitlines() if marker in l]
            for marker in ('GC 检查：','GC 选区汇总：','简单双因素实际集合：')}
    endpoint=load(root/'逐臂采证/m1/space.goal.json')
    full=sorted((z for z in endpoint['Zone'] if z['WP']==z['起点']+z['容量'] and z['有效']>0),
        key=lambda z:z['有效'])
    gap=max(0,GOAL-endpoint['空闲字节']);bound=None
    if gap and full and all(z['容量']==128<<20 for z in full):
        freed=0
        for n,z in enumerate(full,1):
            freed+=z['容量']-z['有效']
            if freed>=gap:
                bound=dict(空间缺口字节=gap,最少需处理当前满区数=n,
                    最低涉及有效字节=sum(x['有效'] for x in full[:n]),
                    相关满区=[{k:x[k] for k in ('编号','有效','容量')} for x in full[:n]],
                    假设='仅回收当前满区；有效数据不失效；不借助其他部分区回收或后续RR；不计目标Finish等额外损失。',
                    性质='静态条件下界，不执行不合格Zone回收，不是实测收尾复制量')
                break
    result=dict(通过=True,来源=str(root),目标字节=GOAL,各臂=reports,
        开启减关闭总迁移字节=delta,结论=conclusion,归档SHA256=state['镜像处理']['archive_sha256'],
        分析器SHA256=digest(Path(__file__)),
        原规则门槛证据=evidence,当前满区静态边界=bound,
        边界='原业务对照已各两次重复，本轮须复现其业务；新增空间收尾只有一对，不声称统计显著性或正常并发性能。')
    (output/'空间结算核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(Path(__file__),output/'分析源码.py')
    rows=[]
    for tag,r in reports.items():
        rows.append(dict(配置=tag,业务迁移字节=r['业务']['总迁移字节'],收尾迁移字节=r['收尾']['总迁移字节'],
            总迁移字节=r['业务加收尾迁移字节'],收尾ZC=r['收尾']['ZC次数'],收尾RR=r['收尾']['RR次数'],
            总Reset=r['业务加收尾Reset'],检查数=r['收尾检查数'],达标=r['目标达成'],
            最终空闲字节=r['最终空闲字节'],超额空间字节=r['超额空间字节']))
    with (output/'空间结算指标.csv').open('x',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
    lines=['# 尾部复用的共同空间下限结算','',conclusion,'',result['边界'],'',
        f'目标启动前固定为{GOAL}字节（{GOAL/(1<<20):.3f}MiB），来源于上一轮关闭组两次相同的末端空闲量。',
        '逐字复用原db_bench、全部ZenFS对象、逻辑输入及业务环境，只新增业务后的独立结算工具。原业务账目必须复现；收尾无前台读写，共同执行原Greedy检查，GC20%、候选和RR1024保持。',
        '', '|指标|尾部关闭|尾部开启|','|---|---:|---:|']
    for label,key,scale in [('业务复制MiB','业务迁移字节',1<<20),('收尾复制MiB','收尾迁移字节',1<<20),
        ('业务加收尾复制MiB','总迁移字节',1<<20),('收尾ZC次数','收尾ZC',1),('收尾RR次数','收尾RR',1),
        ('业务加收尾Reset','总Reset',1),('收尾检查次数','检查数',1),
        ('最终空闲MiB','最终空闲字节',1<<20),('超过共同下限MiB','超额空间字节',1<<20)]:
        lines.append(f'|{label}|{rows[0][key]/scale:.3f}|{rows[1][key]/scale:.3f}|')
    lines.extend(['',f'开启减关闭总复制：{delta}字节（{delta/(1<<20):.3f}MiB）。',
        'Reset包含挂载、维护源区及自然判空等成功Reset；不是直接实测Block擦除。收尾没有前台请求，不生成虚构的业务API延迟或吞吐。读回校验在收尾之后，单独计量。',
        '共同下限不等于完全相等空间；超额空间、有效量和完整复制—映射—Reset事务在JSON中保留。未达标时原样报告，不修改触发条件强行凑结果。',
        '结算使用共同重挂载，在线H的进程内读热历史重新学习；物理RC和W持久来源标签保留，已有协同成员按持久映射恢复。即使本次结算后少复制，也只适用于这一结算路径，不能直接推广为不关闭进程的持续运行长期净收益。',
        '',f'归档SHA256：`{result["归档SHA256"]}`。客机已正常退出，临时镜像状态：{state["镜像处理"]["status"]}。'])
    lines.extend(['','## 原规则为何没有继续回收',''])
    for x in evidence['m1']['GC 选区汇总：']:
        lines.append(f'- 实测检查：空闲比例{x["检查空闲比例"]}，无效比例门槛{x["无效比例门槛"]}，'
            f'满Zone={x["满Zone"]}，最高无效比例{x["最大无效比例"]}，实际选中{x["选中Zone"]}。')
    if bound:
        lines.extend(['',f'当前空间缺口{gap/(1<<20):.3f}MiB。仅考虑当前满区且有效数据不再失效时，'
            f'至少需处理{bound["最少需处理当前满区数"]}个区，涉及至少{bound["最低涉及有效字节"]/(1<<20):.3f}MiB有效数据。',
            '这是静态条件下界，不是实际执行值；不包括其他部分区回收、后续RR或新失效带来的变化。'
            '不能用它补填实测迁移量，也不能据此直接推广为长期结论。'])
    lines.extend(['','## 下一步','',
        '当前先保持尾部复用默认关闭，不为完成本轮目标而降低GC候选门槛。后续应回到共同持续负载，'
        '在相同累计逻辑工作量和完整维护账目下观察空间债务是否持续增长，并在模型评价中同时报告复制、Reset和空间。'
        '本轮没有修改victim模型，不能将结果解释为模型改选收益。'])
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    print(conclusion);print(json.dumps(rows,ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();analyze(a.root,a.output)
