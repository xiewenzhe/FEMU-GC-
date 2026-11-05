"""不限速同负载YCSB双策略验收；不套用旧限速/变负载先导结论。"""
import hashlib
import json
from pathlib import Path
import re
import sys

from analyze_joint_opportunity import fields, model_decisions, require
from analyze_stable_credit import audit_scores
from analyze_sst_probe import latency, gc_coverage
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from rrzc_rr_health import inspect
from ycsb_throughput import throughput


def sizes(stage):
    require(stage in ('smoke','short','long'),'未登记业务阶段')
    return (8192,8192,64,2048,10) if stage=='smoke' else (
        491520,400000 if stage=='short' else 4000000,4096,65536,30)


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1<<20):value.update(block)
    return value.hexdigest()


def analyze_arm(root,stage,mode):
    require(mode in ('greedy','stable'),'未登记策略');root=Path(root)
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
    require((root/'策略.txt').read_text().splitlines()==['策略='+mode,'Greedy开关='+str(int(mode=='greedy')),
        '稳定收益开关='+str(int(mode=='stable'))],'包装执行策略不符')
    require(f'只读全键最新值哈希校验通过，键数={records}' in (root/'verify.log').read_text(),'全键校验未完成')
    verify=(root/'zenfs.verify.log').read_text()
    require('[ERROR]' not in verify and 'RR 开始：' not in verify and 'GC 检查：' not in verify,'校验引入维护或错误')
    sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
    from sst_probe_trace import check
    trace=check(root/'audit.requests',records,hot,ops,0,count)
    ycsb=(root/'ycsb.log').read_text()
    require(ycsb.count('实验WAL配置：关闭=1')==1,'绑定WAL关闭未确认')
    counts=dict(s.split('=',1) for s in (root/'audit.counts').read_text().splitlines())
    require(counts=={'装载':str(records),'读取':str(trace['读取数']),'更新':str(trace['更新数']),'键数':str(records)},'审计计数不符')
    zen=(root/'zenfs.run.log').read_text();lines=zen.splitlines()
    require('统一选区配置：启用=1，' in zen and '公共内容隔离配置：启用=1，' in zen,'机制入口未确认')
    levels=set(re.findall(r'GC 检查：.*启动水平=(\d+)%',zen));require(not levels or levels=={'20'},'GC阈值改变')
    require('[ERROR]' not in zen and '状态=失败' not in zen,'维护错误')
    rock=(root/'aux/rrzc-ycsb/LOG').read_text()
    require(not re.search(r'Background (compaction|flush) error|Zone allocation failure|Corruption:',rock),'后台错误')
    decisions=model_decisions(lines)
    for line in lines:
        if '稳定收益配对模式：' in line:
            r=fields(line,'稳定收益配对模式：')
            require(r['Greedy']==str(int(mode=='greedy')) and r['稳定模型']==str(int(mode=='stable')) and
                    r['共用安全复核']=='1','实际策略分支不符')
    scores=audit_scores(lines,mode) if '统一成本：' in zen else []
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
        实际评分核验=scores,正常模型决策数=len(decisions),正抵扣记录数=sum(r['RR抵扣']>0 for r in scores),
        请求SHA256=digest(root/'audit.requests'),最终值SHA256=digest(root/'audit.expected'),
        边界='4GiB单轮串行YCSB-C++派生，无WAL、不限速；API非原始设备延迟，Update含Get再Put；不预设稳态或设备饱和')


def compare_pair(root,stage):
    root=Path(root);arms={mode:analyze_arm(root/(stage+'-'+mode)/(stage+'-'+mode),stage,mode)
                          for mode in ('greedy','stable')}
    for mode in arms:
        require(json.loads((root/(stage+'-'+mode)/'阶段归档.json').read_text())['完整'],'归档或关机不完整')
    a,b=arms['greedy'],arms['stable']
    require(a['请求SHA256']==b['请求SHA256'] and a['最终值SHA256']==b['最终值SHA256'],'同负载配对请求或最终值不同')
    folders=[root/(stage+'-'+mode)/(stage+'-'+mode) for mode in ('greedy','stable')]
    require((folders[0]/'software.before.log').read_bytes()==(folders[1]/'software.before.log').read_bytes(),'两臂不同库')
    require((folders[0]/'workload.properties').read_bytes()==(folders[1]/'workload.properties').read_bytes(),'两臂不同负载')
    return dict(配对验收通过=True,阶段=stage,模式=arms,
        模型相对Greedy差值={key:b[key]-a[key] for key in ('RR次数','ZC次数','总迁移字节','总成功ZoneReset','推导物理Block擦除次数')},
        边界='同库、同负载、同请求、各臂全新NAND；并非同一内存NAND快照，初始SST布局不保证逐物理页相同；单轮不证明稳定因果收益')
