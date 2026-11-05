"""窗口模型的真实不限速YCSB核验；不改旧分析器和历史结果口径。"""
import hashlib
import json
from pathlib import Path
import re
import sys
import argparse

from analyze_joint_opportunity import fields, model_decisions, require
from analyze_window_credit import audit_scores
from analyze_sst_probe import latency, gc_coverage
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from rrzc_rr_health import inspect
from ycsb_throughput import throughput
from client_phase import phases


def sizes(stage):
    require(stage in ('smoke','short','long'),'未登记业务阶段')
    return (8192,8192,64,2048,10) if stage=='smoke' else (
        491520,400000 if stage=='short' else 4000000,4096,65536,30)


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1<<20):value.update(block)
    return value.hexdigest()


def analyze_arm(root,stage,mode,*,expected_rr=False,strict_rr=False,space_plan=False,space_plan_version=1,
                same_scope=False,joint_time=False,terminal_version=None):
    # 新入口必须显式声明；旧实验的策略、候选和版本校验保持不变。
    require(not same_scope or (space_plan and space_plan_version==3 and joint_time),
            '同范围三组须显式选择时间一致空间方案')
    require(terminal_version is None or (same_scope and terminal_version==2 and mode=='model'),
            '边界YCSB须显式选择第二版模型臂')
    require(mode in (('greedy','space','model') if space_plan else
        ('greedy','expected') if expected_rr else ('greedy','window')),'未登记策略');root=Path(root)
    enabled=mode!='greedy' if same_scope else mode=='model' if space_plan else mode!='greedy'
    planning=space_plan and (same_scope or mode!='greedy')
    records,ops,hot,count,rest=sizes(stage)
    for name in ('wrapper.exit','business.exit','verify.exit','runner.exit'):
        require((root/name).read_text().strip()=='0','未成功：'+name)
    prefix=Path('/home/femu/rrzc-sst-results')/(stage+'-'+mode)
    paths=set()
    for line in (root/'logs.sha256').read_text().splitlines():
        expected,name=line.split(maxsplit=1);relative=Path(name).relative_to(prefix)
        require('..' not in relative.parts and relative not in paths,'哈希路径非法或重复')
        require(digest(root/relative)==expected,'归档哈希错误：'+str(relative));paths.add(relative)
    require({Path(n) for n in ('ycsb.log','zenfs.run.log','audit.requests','audit.expected',
        'device.initial.json','device.before.json','device.after.json','workload.properties','策略.txt')}<=paths,
        '必要证据未进入哈希清单')
    before,after=[(root/f'software.{side}.log').read_text() for side in ('before','after')]
    require(before==after and before and all(s.endswith(': OK') for s in before.splitlines()),'库身份改变')
    properties={}
    for line in (root/'workload.properties').read_text().splitlines():
        if not line or line.startswith('#'):continue
        key,value=line.split('=',1);require(key not in properties,'配置重复：'+key);properties[key]=value
    expected=dict(recordcount=str(records),operationcount=str(ops),sleepafterload=str(rest),
        **{'rrzc.read_key_count':str(hot),'rrzc.update_key_start':'0','rrzc.update_key_count':str(count),
           'limit.ops':'0','rocksdb.disable_wal':'true','rocksdb.no_block_cache':'true',
           'rocksdb.fs_uri':'zenfs://dev:nvme0n1'})
    require(all(properties.get(k)==v for k,v in expected.items()) and not properties.get('limit.file'),
            '实际负载、无WAL/缓存或不限速配置不符')
    policy=['策略='+mode,'Greedy开关='+str(int(not enabled if space_plan else mode=='greedy')),
        '窗口收益开关='+str(int(enabled)),'稳定收益开关=0']
    if expected_rr:policy+=['持续读开关='+str(int(enabled)),'期望RR开关='+str(int(enabled))]
    if space_plan:policy+=['空间方案开关='+str(int(planning)),
        '候选范围='+('expanded' if planning else 'original')]
    require((root/'策略.txt').read_text().splitlines()==policy,'包装执行策略不符')
    require(f'只读全键最新值哈希校验通过，键数={records}' in (root/'verify.log').read_text(),'全键校验未完成')
    verify=(root/'zenfs.verify.log').read_text()
    require('[ERROR]' not in verify and 'GC 检查：' not in verify and
        (strict_rr or 'RR 开始：' not in verify),'校验引入未登记维护或错误')
    if strict_rr:
        require('严格RR主机配置：启用=1，' in verify,'独立数据校验未保持严格RR')
    sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
    from sst_probe_trace import check
    trace=check(root/'audit.requests',records,hot,ops,0,count)
    ycsb=(root/'ycsb.log').read_text()
    require(ycsb.count('实验WAL配置：关闭=1')==1,'绑定WAL关闭未确认')
    counts=dict(s.split('=',1) for s in (root/'audit.counts').read_text().splitlines())
    require(counts=={'装载':str(records),'读取':str(trace['读取数']),'更新':str(trace['更新数']),'键数':str(records)},'审计计数不符')
    zen=(root/'zenfs.run.log').read_text();lines=zen.splitlines()
    if same_scope:
        require(zen.count('时间一致配置：版本=1，启用=1，')==1,'时间一致预测未启用')
        require(zen.count('边界配置：版本=2，启用='+str(int(terminal_version is not None))+'，')==1,
                '边界版本或开关不符')
    require(('严格RR主机配置：启用=1，' in zen)==strict_rr,'新旧RR语义混用，必须选择显式核验入口')
    require('统一选区配置：启用=1，' in zen and '公共内容隔离配置：启用=1，' in zen,'机制入口未确认')
    levels=set(re.findall(r'GC 检查：.*启动水平=(\d+)%',zen));require(not levels or levels=={'20'},'GC阈值改变')
    require('[ERROR]' not in zen and '状态=失败' not in zen,'维护错误')
    rock=(root/'aux/rrzc-ycsb/LOG').read_text()
    require(not re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:',rock),'后台错误')
    if planning:
        if terminal_version is not None:
            from analyze_terminal import audit
            decisions=audit(lines,version=terminal_version)
        elif space_plan_version==3:
            from analyze_deadline import audit
            decisions=audit(lines)
        else:
            from analyze_space_plan import audit_plans
            decisions=audit_plans(lines)
    else:decisions=model_decisions(lines)
    if space_plan:
        require(space_plan_version in (1,2,3) and zen.count('空间方案配置：版本='+str(space_plan_version if planning else 2 if space_plan_version==3 else space_plan_version)+
            '，启用='+str(int(planning))+'，')==1,'空间方案配置不符')
    for line in lines:
        if '稳定收益配对模式：' in line:
            r=fields(line,'稳定收益配对模式：')
            require(r['Greedy']==str(int(not enabled)) and r['稳定模型']=='0' and
                    r['共用安全复核']=='1','实际策略分支不符')
    if enabled:
        require('窗口收益配置：请求=1，启用=1，版本=1，最大支撑窗口=10000000000，先选Greedy=0，原候选资格不变='+str(int(not planning)) in zen,
                '新模型未实际启用')
    else:require('窗口收益配置：' not in zen and '窗口收益模式：' not in zen,'Greedy混入新模型')
    if expected_rr:
        for marker in ('持续读收益配置：','期望RR配置：'):
            require(zen.count(marker)==int(enabled),'新模型实际配置缺失、重复或混入Greedy：'+marker)
        if enabled:
            require('概率尺度=1000000000，先选Greedy=0，泊松假设未校准=1' in zen,'期望RR配置不符')
    else:require('期望RR配置：' not in zen and '期望RR成本：' not in zen,'旧入口拒绝新模型')
    scores=audit_scores(lines,'window' if enabled else 'greedy',
        persistent=expected_rr and enabled,expected_rr=expected_rr and enabled,
        candidate_pool='expanded' if planning else 'original',space_plan=planning,
        deadline=planning and space_plan_version==3,joint_time=joint_time,
        terminal=terminal_version is not None,terminal_version=terminal_version or 1) if '统一成本：' in zen else []
    require((root/'RR调度.txt').read_text().strip()=='等待优先开关=1' and
            'RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in zen,
            '共享RR修复未实际启用')
    require(zen.count('RR公平队列服务：')==zen.count('RR 开始：'),'RR服务与维护账目不符')
    zc=[tuple(map(int,m.groups())) for m in ZC_STATS.finditer(zen)]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(all(r[2]==1 for r in zc+rr),'非单区维护')
    copies=[fields(s,'V1后续迁移复制：') for s in lines if 'V1后续迁移复制：' in s]
    moved=sum(r[1] for r in zc+rr)
    require(all(c['复制成功']=='1' for c in copies) and sum(int(c['有效字节']) for c in copies)==moved and
            zen.count('RR 开始：')==zen.count('RR 完成：')==len(rr),'复制/RR账目不闭合')
    health=inspect(zen);require(health['验收通过'],'RR健康失败：'+str(health['错误']))
    devices=[json.loads((root/f'device.{side}.json').read_text()) for side in ('initial','before','after')]
    require(all(d['每区块数']==128 and d['RR阈值']==1024 and len(d['Zone'])==32 for d in devices),'几何不符')
    require(len({(d['会话高'],d['会话低']) for d in devices})==1,'设备会话改变')
    require(all(z['代际']==0 and z['RC']==0 for z in devices[0]['Zone']),'非全新NAND/RC')
    geometry=json.loads((root/'zones.initial.json').read_text())['zone_list']
    require(len(geometry)==32 and all(z['state']=='EMPTY' and z['wp']==z['slba'] and z['cap']*512==128<<20
            for z in geometry),'非全空4GiB盘')
    first,last=[{z['地址']:z['代际'] for z in d['Zone']} for d in devices[1:]]
    require(first.keys()==last.keys() and all(last[z]>=first[z] for z in first),'代际回退')
    resets=sum(last[z]-first[z] for z in first)
    require(resets==len(re.findall(r'Zone 重置诊断：Zone=\d+，状态=成功，',zen)) and resets>=len(zc)+len(rr),'Reset账目不符')
    return dict(验收通过=True,阶段=stage,策略=mode,真实请求=trace,RR健康=health,
        ZC次数=len(zc),RR次数=len(rr),总迁移字节=moved,ZC迁移字节=sum(r[1] for r in zc),RR迁移字节=sum(r[1] for r in rr),
        总成功ZoneReset=resets,推导物理Block擦除次数=resets*128,
        擦除口径='成功Reset代际增量含自然空区和元数据；乘128为映射推导，非独立硬件计数器',
        API调用延迟=latency(ycsb,ops),吞吐量=throughput(ycsb,ops,properties),GC覆盖=gc_coverage(zen),
        客户端阶段耗时=phases(ycsb,ops),
        实际评分核验=scores,正常模型决策数=len(decisions),正抵扣记录数=sum(r['RR抵扣']>0 for r in scores),
        请求SHA256=digest(root/'audit.requests'),最终值SHA256=digest(root/'audit.expected'),
        边界='4GiB单轮串行YCSB-C++派生，无WAL、不限速；API非原始设备延迟，Update含Get再Put；不预设稳态或设备饱和')


def compare_pair(root,stage):
    root=Path(root);arms={mode:analyze_arm(root/(stage+'-'+mode)/(stage+'-'+mode),stage,mode)
                          for mode in ('greedy','window')}
    for mode in arms:
        state=json.loads((root/(stage+'-'+mode)/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'归档或关机不完整')
    a,b=arms['greedy'],arms['window']
    require(a['请求SHA256']==b['请求SHA256'] and a['最终值SHA256']==b['最终值SHA256'],'同负载配对请求或最终值不同')
    folders=[root/(stage+'-'+mode)/(stage+'-'+mode) for mode in ('greedy','window')]
    require((folders[0]/'software.before.log').read_bytes()==(folders[1]/'software.before.log').read_bytes(),'两臂不同库')
    require((folders[0]/'workload.properties').read_bytes()==(folders[1]/'workload.properties').read_bytes(),'两臂不同负载')
    return dict(配对验收通过=True,阶段=stage,模式=arms,
        模型相对Greedy差值={key:b[key]-a[key] for key in ('RR次数','ZC次数','总迁移字节','总成功ZoneReset','推导物理Block擦除次数')},
        边界='同库、同负载、同请求、各臂全新NAND；并非同一内存NAND快照，初始SST布局不保证逐物理页相同；单轮不证明稳定因果收益')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--stage',choices=('smoke','short','long'),required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖原始或已有核验')
    result=compare_pair(args.root,args.stage)
    result['重新核验说明']='修正诊断集合解释后只读重算；原实验状态、失败日志和所有业务原始数据保持不变'
    state=args.root/'状态.json'
    result['原监督状态']=json.loads(state.read_text()) if state.exists() else None
    result['分析器身份']={p.name:digest(p) for p in (Path(__file__),Path(__file__).with_name('analyze_window_credit.py'),
        Path(__file__).with_name('client_phase.py'),Path(__file__).with_name('replay_window_credit.py'))}
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(配对验收通过=result['配对验收通过'],差值=result['模型相对Greedy差值']),ensure_ascii=False))


if __name__=='__main__':main()
