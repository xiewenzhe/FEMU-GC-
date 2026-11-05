"""有限前缀仅核验实测起点、逐请求和目标扫描；不冒称完整负载收益。"""
import json
from pathlib import Path
import re
from analyze_reproducibility import initial,request_comparison,accounting,transactions
from analyze_repro_allocations import analyze as allocation_diff


def analyze(source,output,rounds,compare_allocations=True):
    output.mkdir(exist_ok=False)
    roots=[source/t for t in ('g1','g2')]
    for r in roots:
        assert (r/'wrapper.exit').read_text().strip()=='0' and json.loads((r/'起始核验.json').read_text())['通过']
        assert 'verifychecksum' in (r/'verify.log').read_text()
        strict=json.loads((r/'strict.business.json').read_text())
        assert strict['历史最高RC']<=1024 and strict['启用']==1,'严格RR阈值或启用状态异常'
        assert '[ERROR]' not in (r/'run.zenfs.log').read_text(),'维护日志出现错误'
        assert '迁移目标扫描：' in (r/'run.zenfs.log').read_text(), '目标扫描诊断未生效'
        rows=re.findall(r'RRZC批次完成：操作=(READ|WRITE)，批次=(\d+)',(r/'run.log').read_text())
        assert set((role,int(n)) for role,n in rows)=={(role,n) for role in ('READ','WRITE') for n in range(rounds)}
    plan=json.loads((source.parent/'预注册计划.json').read_text())
    if plan.get('共同观测'):
        from analyze_all_blocks_dbbench import fields
        for r in roots:
            text=(r/'run.zenfs.log').read_text()
            assert '全块风险配置：启用=1' in text and '全块风险明细：' not in text,'共同查询或明细日志开关不符'
            actual=[fields(line,'共同观测实际决策：') for line in text.splitlines() if '共同观测实际决策：' in line]
            assert actual,'没有共同观测执行记录'
            if not plan.get('模型配对'):
                assert all(x['执行Greedy']=='1' and x['实际存在']==x['Greedy存在'] and
                    x['实际Zone']==x['GreedyZone'] for x in actual),'共同观测Greedy未遵循原排名'
    if plan.get('GC批次检查门'):
        for r in roots:
            log=(r/'run.zenfs.log').read_text()
            assert [int(n) for n in re.findall(r'受控GC检查点开始：批次=(\d+)',log)]==list(range(1,rounds+1)), '边界检查未闭合'
            if rounds==16:assert '受控GC紧急检查：' not in log,'有限前缀发生紧急检查，不宣称后台边界同步'
            assert (r/'受控GC.ack').read_text().strip()==str(rounds),'最终GC确认不符'
    if plan.get('受控空间请求确认'):
        for r in roots:
            log=(r/'run.zenfs.log').read_text()
            sequences=[[int(n) for n in re.findall(marker+r'序号=(\d+)',log)] for marker in
                ('受控空间请求：','受控空间维护完成：','受控空间确认：')]
            assert sequences[0]==sequences[1]==sequences[2]==list(range(1,len(sequences[0])+1)), '空间请求/维护/确认未逐项闭合'
    request=request_comparison(*roots)
    assert request['逻辑提交相同'] and request['完整成功请求数']==[rounds*57344]*2,'有限前缀实际请求不同'
    state=initial(*roots)
    data=dict(实际批次数=rounds,请求=request,起点=state,
        维护={t:accounting(r) for t,r in zip(('g1','g2'),roots)},
        事务={t:transactions(r) for t,r in zip(('g1','g2'),roots)},
        边界='有限前缀诊断，不判定完整负载重复性，不作模型收益；实际目标首差见单独文件')
    if rounds==24:data['边界']='完整24批受控调度诊断，须另核验事件与空间等待；不能代表原10秒周期并发性能或模型收益'
    for t in ('g1','g2'):
        assert data['维护'][t]['复制日志与维护汇总相同'] and data['维护'][t]['映射提交失败数']==0
        assert not data['事务'][t]['未闭合'] and all(x['复制到提交再源Reset'] for x in data['事务'][t]['完成维护'])
    (output/'核验.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    if compare_allocations:allocation_diff(source,output/'目标首差.json')
    return dict(逐请求相同=True,请求数=rounds*57344,维护=data['维护'],边界=data['边界'])
