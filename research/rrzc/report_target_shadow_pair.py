"""采证结束后自动汇总真实账目；不以候选有效量差冒称反事实收益。"""
import json
from pathlib import Path

from ycsb_af import require

MODES=('G2','G2_WSURVIVOR')


def report(pair, output):
    state=json.loads((pair/'状态.json').read_text())
    require(state.get('完整实验完成'),'未完成配对，不生成结论')
    proof=json.loads((pair/'配对分析/完整核验.json').read_text())
    require(proof['通过'],'输入/起点/账目未通过')
    data={m:json.loads((pair/m/'业务核验.json').read_text()) for m in MODES}
    chain=json.loads((output/'实际成本链/实际去向与首差核验.json').read_text())
    new=data[MODES[1]];gates=[r for r in new['同次目标影子']['逐次'] if r['本次历史拒绝']]
    before=(chain['首次实际目标差异']['各臂'][MODES[1]]['事件'] <
            chain['首次新规则拒绝']['尝试事件']) if chain['首次新规则拒绝'] and chain['首次实际目标差异'] and '各臂' in chain['首次实际目标差异'] else None
    conclusions=dict(完整配对通过=True,原始目录=str(pair),历史拒绝数=len(gates),
        拒绝中有已观察普通部分区建议数=sum(r['普通部分区扫描建议']=='1' for r in gates),
        拒绝中普通建议与实际同身份数=sum(r['普通建议与实际同身份'] for r in gates),
        拒绝中两个候选字段可比较数=sum(r['两个已观察候选字段可比较'] for r in gates),
        拒绝中普通候选当前有效更少数=sum(r['普通候选当前有效更少'] for r in gates),
        拒绝中完整观察数=sum(r['观察完整']=='1' for r in gates),
        首次目标分歧早于门控=before,总迁移观察变化百分比=proof['总迁移变化百分比'],
        原G2入池建议被覆盖数=new['旧G2影子']['拒绝覆盖原入池建议数'],
        不将单对差额宣布为因果收益=True,不将候选有效量差当作节省=True)
    text=['# 同次目标完整采证：结果与判断','',
        '本轮复用已验收双目标观察程序，G2和W历史准入各一臂，串行独立客机；不调整算法、阈值或K，没有额外Greedy重复。','',
        f'原始配对：`{pair}`。输入起点、逐请求及复制—映射—Reset账目通过。','',
        '## 本次实际比较了什么','',
        '两臂共同开启ZENFS_RRZC_TARGET_SHADOW=1。原Choose仍执行原策略，普通部分区只读建议不申请令牌、不Commit、不执行预留/Finish；busy和代际未知单列。候选当前有效量描述既有陪放数据，不是本次 incoming 文件复制量，也不等于可节省字节。','',
        '|指标|G2|W历史准入|','|---|---:|---:|']
    for label,key,unit in [('ZC+RR复制MiB','总迁移字节',1<<20),('RR复制MiB','RR迁移字节',1<<20),
        ('ZC复制MiB','ZC迁移字节',1<<20),('RR次数','RR次数',1),('ZC次数','ZC次数',1),
        ('成功Reset','全部成功Reset次数',1)]:
        values=[data[m]['维护及资源']['维护'][key]/unit for m in MODES]
        text.append('|'+label+'|'+'|'.join(f'{v:.3f}' if unit!=1 else str(int(v)) for v in values)+'|')
    for label,fn in [('末端可写MiB',lambda r:r['维护及资源']['末端空间']['空闲字节']/(1<<20)),
                     ('末端有效MiB',lambda r:r['维护及资源']['末端空间']['有效字节']/(1<<20)),
                     ('应用完成吞吐ops/s',lambda r:r['应用端到端']['应用完成吞吐ops']),
                     ('aux实际调用秒',lambda r:r['aux实际调用']['调用累计秒'])]:
        text.append('|'+label+'|'+'|'.join(f'{fn(data[m]):.3f}' for m in MODES)+'|')
    text+=['',f"总复制观察变化{proof['总迁移变化百分比']:+.3f}%；空间和复制分别报告，不相减成所谓净迁移。Reset含全无效区回收，不直接代表物理Block实测擦除。",'',
        '## 双目标观察与历史拒绝','',
        '|观察指标|G2|W历史准入|','|---|---:|---:|']
    for key in ('观察数','完整观察数','有已观察普通部分区建议数','两个已观察候选字段可比较数',
                '普通候选当前有效更少数','历史拒绝数'):
        text.append('|'+key+'|'+'|'.join(str(data[m]['同次目标影子'][key]) for m in MODES)+'|')
    text+=['',f"历史拒绝{len(gates)}次，其中覆盖原G2入池建议{conclusions['原G2入池建议被覆盖数']}次；有普通部分区建议{conclusions['拒绝中有已观察普通部分区建议数']}次，与实际普通目标同Zone/代际{conclusions['拒绝中普通建议与实际同身份数']}次。",'',
        f"拒绝中两个已观察候选可比较{conclusions['拒绝中两个候选字段可比较数']}次，普通候选当前有效更少{conclusions['拒绝中普通候选当前有效更少数']}次，完整观察{conclusions['拒绝中完整观察数']}次。",'',
        '|文件ID|旧G2建议Zone/代际|普通扫描建议Zone/代际|实际Zone/代际|普通有效减协同字节|观察完整|',
        '|---|---|---|---|---:|---|']
    for r in gates:
        ordinary=(f"Z{int(r['普通建议Zone'])//(1<<27)}/g{r['普通代际']}" if r['普通部分区扫描建议']=='1' else '无已观察部分区；后续未知')
        cooperative=(f"Z{int(r['协同建议Zone'])//(1<<27)}/g{r['协同代际']}" if r['协同建议动作']!='0' else '原建议回退，无Zone目标')
        text.append(f"|{r['文件编号']}|{cooperative}|{ordinary}|Z{r['实际Zone']//(1<<27)}/g{r['实际代际']}|{r['当前有效差普通减协同']}|{r['观察完整']}|")
    text+=['','没有完整观察时，只能比较列出的两个候选，不能断言是全盘最便宜目标。新写的部分区扫描之后还可能被原Active/寿命/Empty规则舍弃；必要迁移还可能走原预留路径。影子Zone=0占位不是元数据分配。','',
        '## 实际后续成本和因果边界','',
        f"拒绝后文件本轮再次复制关联数{chain['拒绝后仍再复制次数']}；收尾前观察退役关联数{chain['收尾前观察到退役文件数']}，其后extent释放关联数{chain['退役后观察到Clear文件数']}。逐笔源/目标代际见实际成本链CSV，不扩展到观察窗外。",'',
        f'首次目标分歧早于首次历史拒绝：{before}。完整首差见`实际成本链/实际去向与首差核验.json`；若为True，完整复制差额没有隔离成规则的因果收益。若为未知，也不能宣称首差已排除。','',
        '候选有效量更少不证明风险、后续寿命和空间代价更低；实际目标已有H/W组成来自现有离线映射，非跨层原子快照。新观测有池Snapshot和日志开销，没有独立开关配对测得其净开销，不与旧轮耗时混用。','',
        'API和应用完成延迟分别在CSV中，含队列/批Flush等待的应用完成耗时不是介质单次响应。aux调用差额没有进一步分离宿主同步/调度，吞吐差额不能自动归为迁移优化收益。','',
        '## 当前交付与下一步边界','',
        '本轮是补齐比较证据，不是新增准入算法。先据具体拒绝事件回答“候选真的更便宜，还是成本被搬去普通区”；不根据单对百分比调阈值或K。现有观察未覆盖普通预留的完整后续时，就保留该缺口，不把未知当成已解决。','',
        '保留原G2、当前W历史候选和本轮程序。没有自动启动额外实验；归档退出、临时镜像回收及哈希见每臂状态和镜像生命周期。']
    output.mkdir(parents=True,exist_ok=True)
    (output/'实施与结果分析.md').write_text('\n'.join(text)+'\n')
    (output/'采证结论.json').write_text(json.dumps(conclusions,ensure_ascii=False,indent=2)+'\n')
    for name in ('同次建议与实际目标.csv','历史拒绝双目标比较.csv','同次目标影子核验.json',
                 '维护资源吞吐.csv','API延迟.csv','端到端延迟.csv','完整核验.json','影子建议核验.json'):
        source=pair/'配对分析'/name;target=output/name
        if source.exists() and not target.exists():target.write_bytes(source.read_bytes())
    return conclusions
