"""定制热点db_bench的Greedy/模型先导配对；真实同区程度及联合机会独立报告。"""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import time

from run_dbbench_parallel_host import DbRun, ROOT, IMAGE_DIR, stamp
from dbbench_suite import cpu_sets, wrapper_source as native_wrapper
from run_ycsb_quick_host import disk_space
from run_ycsb_af_host import resource_sample
from ycsb_af import require, digest

RECORDS=1048576
READS=400000
WRITES=600000


def once(text,before,after):
    require(text.count(before)==1,'独立热点接线源结构变化：'+before[:90])
    return text.replace(before,after,1)


def wrapper_source(writes=WRITES):
    require(isinstance(writes,int) and writes>0,'写操作预算必须是正整数')
    text=native_wrapper()
    text=once(text,'enabled=0; greedy=1','enabled=0; greedy=1; hotspot=0; readkeys=4096; writekeys=61440\n'
        '[[ $tag != smoke-* ]] || { readkeys=64; writekeys=960; }\nplanning=()')
    text=once(text,'if [[ $mode == model ]]; then enabled=1; greedy=0; fi',
        'if [[ $mode == model ]]; then enabled=1; greedy=0; planning=(ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0); fi')
    text=once(text,'ZENFS_RRZC_CANDIDATE_POOL=expanded ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 \\\n  ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0 \\',
        'ZENFS_RRZC_CANDIDATE_POOL=expanded "${planning[@]}" \\\n  RRZC_DB_HOTSPOT="$hotspot" RRZC_DB_READ_KEYS="$readkeys" RRZC_DB_WRITE_KEYS="$writekeys" \\\n  RRZC_DB_WRITE_HOT_PERCENT=80 RRZC_DB_LAYOUT_PREFIX="$result/布局" \\')
    text=once(text,'writes=150000','writes='+str(writes))
    text=once(text,'business=(--benchmarks=readwhilewriting',
        'hotspot=1\n  business=(--benchmarks=rrzclayout,readwhilewriting,rrzclayout')
    text=once(text,'printf \'verify\\n\' > "$result/phase.txt"',
        'hotspot=0\nprintf \'verify\\n\' > "$result/phase.txt"')
    return text


def analysis_source(writes=WRITES):
    require(isinstance(writes,int) and writes>0,'核验写预算必须是正整数')
    text=(ROOT/'analyze_dbbench.py').read_text()
    text=once(text,'from analyze_joint_opportunity import fields',
        'from analyze_joint_opportunity import fields, model_decisions')
    text=once(text,'else 150000','else '+str(writes))
    text=once(text,"'时间一致配置：版本=1，启用=1，'",
        "'时间一致配置：版本=1，启用='+str(int(enabled))+'，'")
    text=once(text,'plans = audit(lines); forecasts = audit_forecasts(lines, time_consistent=True)',
        'plans = audit(lines) if enabled else model_decisions(lines)\n'
        '        forecasts = audit_forecasts(lines, time_consistent=True) if enabled else []\n'
        '        if not enabled: audit_greedy(lines)')
    text=once(text,"candidate_pool='expanded', space_plan=True, deadline=True,\n                joint_time=True",
        "candidate_pool='expanded', space_plan=enabled, deadline=enabled,\n                joint_time=enabled")
    return text+'''

def audit_greedy(lines):
    for plan in model_decisions(lines):
        safe=[r for r in plan['成本'] if r['安全']=='1']
        require(all(int(r['有效'])+int(r['回收'])==128<<20 and int(r['RR抵扣'])==0 for r in safe),
                'Greedy候选容量或零RR成本不符')
        expected=min(safe,key=lambda r:(int(r['有效']),int(r['Zone']))) if safe else None
        d=plan['决策']
        require(int(d['实际Zone'])==(int(expected['Zone']) if expected else 0),
                '基线实际选区不是最少有效量Greedy')
'''


def layout(root,readkeys):
    rows=[]
    for source in sorted(root.glob('布局-*.sst.csv')):
        ext=source.with_name(source.name.replace('.sst.csv','.extent.csv'))
        require(ext.is_file(),'SST映射没有对应真实Zone片段')
        files={}
        with source.open() as stream:
            for r in csv.DictReader(stream):
                low=int.from_bytes(bytes.fromhex(r['最小键十六进制'])[:8],'big')
                high=int.from_bytes(bytes.fromhex(r['最大键十六进制'])[:8],'big')
                files[Path(r['文件']).name]=(low<readkeys,high>=readkeys and low<readkeys*16)
        zones={}
        with ext.open() as stream:
            for r in csv.DictReader(stream):
                kind=files.get(Path(r['文件']).name)
                if kind:
                    item=zones.setdefault(int(r['Zone']),[False,False,[]])
                    item[0]|=kind[0];item[1]|=kind[1];item[2].append(r['文件'])
        rows.append(dict(快照=source.name,同区范围覆盖=[z for z,v in zones.items() if v[0] and v[1]],
            读范围覆盖Zone=[z for z,v in zones.items() if v[0]],
            边界='按SST键边界与实际extent关联；采样非原子，不能证明每次读取位置或同区全部数据均为热数据'))
    require(len(rows)>=2,'缺少业务前后SST/Zone布局证据')
    return rows


def freeze(output,*,writes=WRITES,capture_source=None):
    folder=output/'源码'
    for source in ROOT.glob('*.py'):shutil.copyfile(source,folder/source.name)
    for name in ('dbbench_hotspot_suite.py','capture_dbbench_guest.py','capture_ycsb_stream_guest.py',
                 'strict_stats_guest.py','test_dbbench_hotspot.py'):
        shutil.copyfile(ROOT/'tests'/name,folder/name)
    (folder/'run_dbbench_guest.sh').write_text(wrapper_source(writes))
    (folder/'analyze_dbbench_hotspot.py').write_text(analysis_source(writes))
    if capture_source is not None:
        (folder/'capture_dbbench_guest.py').write_text(capture_source)
    expected=json.loads((output/'同库身份.json').read_text())
    target='/home/femu/'+output.name.removesuffix('-results')
    for name in ('run_dbbench_guest.sh','capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py'):
        expected[target+'/'+name]=digest(folder/name)
    stamp(output/'运行软件身份.json',expected)
    (folder/'software.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in expected.items()))


def module(path):
    spec=importlib.util.spec_from_file_location('hotspot_analyzer',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def worker(args,password,output,stage,mode,slot,cpus,*,parent_image=None,read_count=READS,checkpoints=False,
           smoke_records=8192,smoke_reads=8192,analyzer_name='analyze_dbbench_hotspot.py',
           analyze_hook=None,layout_read_keys=None):
    os.sched_setaffinity(0,cpus)
    tag=stage+'-hot-'+mode;out=output/tag;out.mkdir()
    run=DbRun(args,password,out,tag,8101+slot,parent_image or IMAGE_DIR/(args.label+'-prepare.qcow2'))
    remote=run.target+'/'+tag;error=None
    try:
        run.record(CPU集合=cpus,策略='最少有效量Greedy' if mode=='space' else '原去边界联合成本模型')
        run.start()
        if parent_image is not None:
            # 复用其他标签的软件父镜像时，新标签目录必须独立创建，不覆盖旧试次。
            run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
            run.deployed=True
        run.command(run.scp()+[str(output/'源码'/n) for n in ('run_dbbench_guest.sh',
            'capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py','software.sha256')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.deployed=True
        records,reads=(smoke_records,smoke_reads) if stage=='smoke' else (RECORDS,read_count)
        run.root(f'nohup setsid bash {run.target}/run_dbbench_guest.sh {tag} {mode} hot {records} {reads} '
            f'< /dev/null > {run.target}/{tag}.启动.log 2>&1 &')
        started=changed=time.monotonic();previous=None
        while time.monotonic()-started<7200:
            disk_space()
            free=int(run.ssh("python3 -c 'import os;s=os.statvfs(\"/\");print(s.f_bavail*s.f_frsize)'"))
            require(free>=6<<30,'客机根盘不足6GiB，提前采证关机')
            text=run.root(f'python3 {run.target}/capture_dbbench_guest.py progress {remote}',35)
            point=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
            if checkpoints:
                # 多文件顺序采样，操作数是已报告下界，不伪称精确逐操作切点。
                with (out/'操作进度采样.jsonl').open('a') as stream:
                    stream.write(json.dumps(dict(UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                        观测=point),ensure_ascii=False)+'\n')
            run.record(阶段=tag+'：'+point['阶段'],最近观测=point,
                资源观测=resource_sample(run.state['当前客机PID']))
            require(not point['错误'],'真实业务或后台错误')
            if point['完成']:break
            key=point['阶段'],point['进度']
            if key!=previous:previous,changed=key,time.monotonic()
            require(time.monotonic()-changed<180,'180秒请求无进展，停止并采证')
            time.sleep(10)
        else:raise RuntimeError('超过先导登记上限')
    except BaseException as failure:
        error=failure;run.record(原始错误=repr(failure));raise
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=str(cleanup))
    root=out/args.label/tag
    analyzer=module(output/'源码'/analyzer_name)
    data=analyzer.analyze(root,tag,'hot',mode,records,reads)
    data['策略名称']='Greedy（同候选与安全检查）' if mode=='space' else '去边界联合成本模型'
    data['布局核验']=layout(root,layout_read_keys if layout_read_keys is not None else 64 if stage=='smoke' else 4096)
    data['初态有同区范围覆盖']=bool(data['布局核验'][0]['同区范围覆盖'])
    if analyze_hook is not None:analyze_hook(root,data,stage)
    stamp(out/'独立核验.json',data)
    run.record(阶段=tag+'独立验收完成，客机退出',最近结果={k:data[k] for k in
        ('RR次数','ZC次数','总迁移字节','RR独立改选次数','初态有同区范围覆盖')})


def execute(args,password,output,budget):
    freeze(output)
    sets=cpu_sets(budget['逻辑CPU'],2)
    state=dict(监督进程PID=os.getpid(),阶段='Greedy/模型并行小测',最大并行=2,
        CPU集合=sets,RR阈值=1024,GC启动百分比=20,已完成=[],运行任务={})
    running={}
    def record():
        state['运行任务']={str(pid):tag for pid,tag in running.items()}
        state['更新时间UTC']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        stamp(output/'状态.json',state)
    try:
        for stage in ('smoke','full'):
            state['阶段']=stage+'局部热点Greedy/模型配对'
            for slot,mode in enumerate(('space','model')):
                pid=os.fork()
                if pid==0:
                    try:worker(args,password,output,stage,mode,slot,sets[slot])
                    except BaseException as error:
                        print(stage,mode,'原始错误：',repr(error),flush=True);os._exit(1)
                    os._exit(0)
                running[pid]=stage+'-hot-'+mode;record()
            while running:
                for pid in list(running):
                    ended,status=os.waitpid(pid,os.WNOHANG)
                    if ended:
                        tag=running.pop(pid)
                        require(os.waitstatus_to_exitcode(status)==0,'任务失败，停止后续：'+tag)
                        state['已完成'].append(tag);record()
                if running:time.sleep(5)
            arms={m:json.loads((output/(stage+'-hot-'+m)/'独立核验.json').read_text()) for m in ('space','model')}
            for key in ('公共参数SHA256','业务参数SHA256','实际读次数','实际写次数'):
                require(arms['space'][key]==arms['model'][key],'负载配对不一致：'+key)
            pair=dict(配对验收通过=True,阶段=stage,工作负载='hot',模式=arms,
                联合机会初筛=any(r['RR次数']>0 and r['ZC次数']>0 for r in arms.values()),
                说明='space实际为最少有效量Greedy；定制db_bench并行先导，不是原生公开负载或稳态结论')
            stamp(output/(stage+'-hot-配对核验.json'),pair)
            if stage=='full':
                from plot_dbbench_normalized import render
                render(output,baseline_description='基线为同候选及安全检查下的最少有效量Greedy，非stock批量GC')
                state['联合机会初筛']=pair['联合机会初筛']
            record()
        state['阶段']='局部热点先导完成，已出归一化图，客机全部退出';record()
    except BaseException as error:
        state['错误']=repr(error);state['阶段']='先导停止，正在正常收尾';record();raise
    finally:
        for pid in running:
            if Path('/proc',str(pid)).exists():os.kill(pid,signal.SIGTERM)
        while running:
            for pid in list(running):
                ended,_=os.waitpid(pid,os.WNOHANG)
                if ended:running.pop(pid)
            record()
            if running:time.sleep(5)
        record()
