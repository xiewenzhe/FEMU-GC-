"""归纳复验与单因素时序变化；结果不一致也保留，不挑选成功样本。"""
import re
from statistics import mean
from analyze_guarded_stream import window_account
from analyze_tail_stream import ledger
from analyze_v1_victim_value import stamp
from analyze_v1_shadow_stream import require

def complete_account(lines,copies,operations,result):
    """单独核算全部日志，不能把退出等待中的维护藏在180秒窗口之外。"""
    origins=[stamp(s)-int(m[1])/1e9 for s in lines
             if (m:=re.search(r'滚动更新提交：代号=\d+，经过纳秒=(\d+)',s))]
    require(origins and max(origins)-min(origins)<.020,'全程账目时钟未对齐')
    origin=mean(origins)
    times=[stamp(s)-origin for s in lines if re.match(r'\d{4}/\d\d/\d\d-',s)]
    require(times,'全程日志没有时间戳')
    # 区间只用于包住全部已记录事件，不延长负载或凭空补一个未来观察窗。
    full=window_account(lines,copies,operations,min(0,min(times))-1,max(times)+1)
    for key,expected in (('总迁移MiB',result['全程迁移MiB']),
                         ('ZC次数',result['全程ZC']),('RR次数',result['全程RR'])):
        require(full[key]==expected,'全程复制、完成记录和总账不一致：'+key)
    full.pop('开始秒');full.pop('结束秒')
    full.update(口径='业务日志全程，包含析构等待中完成的维护，不是延长后的稳态窗口',
                最后日志相对业务秒=max(times))
    return full

def final_free_mib(report):
    """从退出后只读Zone报告求可追加空间；不冒充有效量或满区可回收量。"""
    zones=report['zone_list'];capacity=524288
    require(report['nr_zones']==32 and len(zones)==32 and
            all(z['slba']==i*capacity and z['cap']==capacity for i,z in enumerate(zones)),
            '退出后设备几何不符')
    free=0
    for z in zones[3:]:
        if z['state']=='EMPTY':
            require(z['wp']==z['slba'],'空区写指针不符');free+=capacity
        elif z['state'] in ('IMP_OPENED','EXP_OPENED','CLOSED'):
            require(z['slba']<=z['wp']<=z['slba']+capacity,'部分区写指针越界')
            free+=z['slba']+capacity-z['wp']
        else:require(z['state']=='FULL','未知设备区状态')
    return free*512/(1<<20)

def audit_arm(run,result):
    """原始归档不覆盖，另存窗口／全程／退出后设备状态的对照。"""
    import json
    from analyze_stream import rows
    lines=(run/'zenfs.run.log').read_text().splitlines();events=ledger(lines)
    full=complete_account(lines,events['实际复制'],rows(run/'operations.csv'),result)
    require(full['重复迁移MiB']==events['重复文件复制字节']/(1<<20),'全程重复复制账目不符')
    keys=('ZC次数','RR次数','总迁移MiB','重复迁移MiB','全部IO区Reset')
    return dict(固定180秒账目=result['账目'],全程账目=full,
        窗口外维护差额={k:full[k]-result['账目'][k] for k in keys},
        最后业务采样=result['终态空间'],
        退出后设备可追加MiB=final_free_mib(json.loads((run/'zones.final.json').read_text())),
        逐文件=result['逐文件'])

def measures(r):
    return dict(迁移MiB=r['全程迁移MiB'],ZC=r['全程ZC'],RR=r['全程RR'],
        重复MiB=r['账目']['重复迁移MiB'],Reset=r['账目']['全部IO区Reset'],
        空闲字节=int(r['终态空间']['空闲字节']))

def summarize(original,trials):
    require(set(trials)=={'repeat','delay20'},'缺固定对照条件')
    baseline=trials['repeat']['各组']['greedy']
    core=lambda r:{k:v for k,v in r['软件身份'].items() if not k.startswith('/')}
    repeat={}
    for trial,comparison in trials.items():
        for mode,r in comparison['各组'].items():
            require(r['请求身份']==baseline['请求身份'] and r['软件身份']==baseline['软件身份'],
                    '六组逻辑请求或软件身份不一致')
            require(core(r)==core(original['各组'][mode]) and r['请求身份']==original['各组'][mode]['请求身份'],
                    '旧组与复验的生产核心或逻辑请求不同')
            if trial=='repeat':
                old,new=measures(original['各组'][mode]),measures(r)
                repeat[mode]=dict(原始=old,复验=new,逐项一致=old==new)
    return dict(核验通过=True,原始与复跑=repeat,固定条件结果=trials,
        解释='两次原时序观测用于初步重复性；删除推迟20秒用于单因素敏感性。未改机制、读停止点、读写请求或最终文件集合。不是稳态或统计显著性结论。')

def audit_archives(root):
    """重新校验六份原始归档，再输出独立全程口径；不覆盖监督器的历史汇总。"""
    import hashlib,json
    from pathlib import Path
    from analyze_lifetime import analyze,compare
    normalized=lambda v:json.loads(json.dumps(v,ensure_ascii=False))
    old=root.parent/'lifetime-20261016'
    original=json.loads((old/'三组对照.json').read_text())
    for mode,r in original['各组'].items():
        require(normalized(analyze(Path(r['来源']),mode,r['软件身份']))==r,'旧寿命对照复算发生变化')
    core={n:d for n,d in json.loads((root.parent/'cost-rank-20261016/同库身份.json').read_text()).items()
          if not n.startswith('/')}
    guest='/home/femu/rrzc-lifetime-validation-20261016-tests'
    archive=root/'repeat/greedy'/Path(guest).name
    expected=dict(core)
    for n in ('lifetime_workload_test.cc','lifetime_workload_plan.h','competition_workload_plan.h',
              'run_lifetime_trials_guest.sh','lifetime_workload_test'):
        expected[f'{guest}/{n}']=hashlib.sha256((archive/n).read_bytes()).hexdigest()
    trials={};audits={}
    for trial in ('repeat','delay20'):
        arms={};audits[trial]={}
        for mode in ('greedy','eta','cost'):
            directory=root/trial/mode;run=directory/f'lifetime-{trial}-20261016-{mode}'
            r=analyze(run,mode,expected,trial=trial)
            require(normalized(r)==json.loads((directory/'独立核验.json').read_text()),'本轮归档复算不一致')
            arms[mode]=r;audits[trial][mode]=audit_arm(run,r)
        trials[trial]=compare(arms)
        require(normalized(trials[trial])==json.loads((root/trial/'三组对照.json').read_text()),'三组复算不一致')
    repeated=summarize(original,trials)
    require(normalized(repeated)==json.loads((root/'重复性与时序对照.json').read_text()),'六组原汇总复算不一致')
    keys=('ZC次数','RR次数','总迁移MiB','重复迁移MiB','全部IO区Reset')
    differences={trial:{mode:{k:r['全程账目'][k]-arms['greedy']['全程账目'][k] for k in keys}
                        for mode,r in arms.items()} for trial,arms in audits.items()}
    return dict(核验通过=True,原始与复跑=repeated['原始与复跑'],各组=audits,全程相对Greedy=differences,
        统计源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        说明='原监督汇总保留不覆盖；其中账目与相对Greedy按0至180秒计，逐文件和全程迁移含退出等待。此文件分别列两种口径，退出后可追加空间取NVMe报告，不将最后业务采样冒充退出后状态。')

if __name__=='__main__':
    import json,sys
    from pathlib import Path
    require(len(sys.argv)==2,'需要六组固定实验的归档根目录')
    root=Path(sys.argv[1]).resolve()
    require(root.name=='lifetime-validation-20261016','不是本轮固定归档')
    output=root/'全程独立复核.json'
    require(not output.exists(),'复核结果已存在，拒绝覆盖')
    result=audit_archives(root)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('六组原始归档与旧结果复算通过；已另存全程独立复核，原始汇总未覆盖。')
