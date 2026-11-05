"""从已归档真实候选重算独立评分；不会生成反事实RR、迁移或性能结果。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from analyze_colocation_cases import business_log
from analyze_all_blocks_dbbench import fields

ROOT=Path(__file__).resolve().parent
BASE=Path('/home/data/xwz/rrzc-baselines')
DEFAULTS=tuple(BASE/f'joint-controlled-colocation-{name}-20261029-results'
               for name in ('choice-v3','credit-v2'))


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1<<20),b''):value.update(chunk)
    return value.hexdigest()


def compile_reader(binary):
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
        '-I'+str(ROOT/'model'),str(ROOT/'tests/maintenance_cover_replay.cc'),'-o',str(binary)],check=True)


def snapshots(root):
    records={}
    for line in business_log(root).splitlines():
        for marker,key in (('时序成本：','候选'),('时序方案需求：','需求'),
                           ('共同观测实际决策：','原实际决策')):
            if marker not in line:continue
            row={k:int(v) for k,v in fields(line,marker).items()};n=row['轮次']
            record=records.setdefault(n,dict(轮次=n,候选=[]))
            if key=='候选':record[key].append(row)
            else:
                if key in record:raise ValueError('同轮出现重复字段：'+key)
                record[key]=row
    for r in records.values():
        if '需求' not in r or '原实际决策' not in r:raise ValueError('历史选择字段不完整')
    return list(records.values())


def evaluate(binary,records):
    inputs=[]
    for r in records:
        d=r['需求'];inputs.append(f"{len(r['候选'])} {d['空闲']} {d['非空闲']} {d['分配等待']} {d['目标百分比']}")
        for x in r['候选']:
            inputs.append(' '.join(str(x[k]) for k in ('Zone','有效','容量','RR复制','安全','风险可知')))
    output=iter(subprocess.check_output([str(binary)],input='\n'.join(inputs)+'\n',text=True).splitlines())
    result=[]
    for record in records:
        row=next(output).split();assert row[0]=='D' and len(row)==9
        found,zone,invalid,unknown,known,target,gap,count=map(int,row[1:])
        plans=[]
        for _ in range(count):
            row=next(output).split();assert row[0]=='P'
            first,covered,copy,rr,reclaimed,deferred,n,*zones=map(int,row[1:]);assert len(zones)==n
            plans.append(dict(首区=first,覆盖=bool(covered),当前计划复制=copy,计划外预计RR复制=rr,
                评分总复制=copy+rr,静态回收=reclaimed,计划外当前有效量=deferred,计划区=zones))
        result.append(dict(**record,新建议=dict(存在=bool(found),Zone=zone,输入非法=bool(invalid),
            仅空间进展=bool(unknown),目标可知=bool(known),退出目标=target,退出缺口=gap,计划=plans)))
    assert next(output,None) is None
    return result


def replay(sources,output):
    if output.exists():raise ValueError('不覆盖已有重算结果')
    proofs={};arms=[]
    with tempfile.TemporaryDirectory(prefix='rrzc-cover-') as temp:
        binary=Path(temp)/'reader';compile_reader(binary)
        for source in sources:
            state=json.loads((source/'状态.json').read_text());archive=json.loads((source/'阶段归档.json').read_text())
            assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
            assert state['当前客机PID'] is None
            saved=Path(state['镜像处理']['archive']);sha=state['镜像处理']['archive_sha256']
            assert digest(saved)==sha,'归档哈希不符'
            proofs[str(source)]=dict(归档=str(saved),归档SHA256=sha)
            for arm in sorted((source/state['标签']).glob('*-r[12]')):
                if not arm.is_dir() or arm.name.startswith('smoke-'):continue
                log=arm/'zenfs.log'
                records=snapshots(arm)
                arms.append(dict(来源=str(arm),日志SHA256=digest(log),逐轮=evaluate(binary,records)))
    identity={p.name:digest(p) for p in (ROOT/'model/rrzc_maintenance_cover.h',
        ROOT/'tests/maintenance_cover_replay.cc',Path(__file__))}
    result=dict(通过=True,边界='真实历史状态上的离线重评分；不是执行新策略后的反事实轨迹或净收益',
        源归档=proofs,实现SHA256=identity,各臂=arms)
    output.mkdir(parents=True)
    (output/'历史候选重算.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# 同空间目标评分：历史候选离线重算','',result['边界'],'',
        '|历史臂|轮次|原实际Zone|新建议Zone|覆盖目标|计划复制MiB|计划外RR估计MiB|待维护当前有效MiB|',
        '|---|---:|---:|---:|---|---:|---:|---:|']
    for arm in arms:
        for r in arm['逐轮']:
            d=r['新建议'];p=next((p for p in d['计划'] if p['首区']==d['Zone']),None)
            if p is None:continue
            old=r['原实际决策'];z=128*2**20
            lines.append(f"|{Path(arm['来源']).name}|{r['轮次']}|{old['实际Zone']//z}|{d['Zone']//z}|"
                f"{'是' if p['覆盖'] else '否'}|{p['当前计划复制']/2**20:.3f}|"
                f"{p['计划外预计RR复制']/2**20:.3f}|{p['计划外当前有效量']/2**20:.3f}|")
    lines+=['','这里只能确认历史状态下的评分改变。新策略一旦改选，后续状态可能不同，'
        '不能把后续历史帧拼成新运行，也不能据此宣称减少了RR或复制。',
        '退出目标按当前总空间的21%计算，以匹配原整数百分比<=20的检查线；'
        '旧日志20%目标保留作来源。所有空间覆盖只是字节估计，不证明Open/Active或目标区可分配。']
    (output/'离线重算说明.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,action='append')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();r=replay(args.source or DEFAULTS,args.output)
    print(json.dumps(dict(通过=r['通过'],臂数=len(r['各臂']),输出=str(args.output)),ensure_ascii=False))
