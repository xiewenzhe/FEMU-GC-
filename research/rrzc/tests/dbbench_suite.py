"""db_bench配对调度：同一负载两臂优先并行，失败停止排队并收尾已启动任务。"""
import json
import os
from pathlib import Path
import shutil
import signal
import time
from run_dbbench_parallel_host import DbRun, ROOT, IMAGE_DIR, RECORDS, OLD_TOOLS, stamp
from run_ycsb_noboundary_host import wrapper_source as old_wrapper
from run_ycsb_af_host import resource_sample
from run_ycsb_quick_host import disk_space
from ycsb_af import digest, require
from analyze_dbbench import analyze

CASES = {'hot': 1200000, 'mixed95': 1200000, 'mixed50': 300000}
MODES = ('space', 'model')


def cpu_sets(cpus, maximum, topology=None):
    if topology is None:
        topology = {c:(Path(f'/sys/devices/system/cpu/cpu{c}/topology/physical_package_id').read_text().strip(),
                       Path(f'/sys/devices/system/cpu/cpu{c}/topology/core_id').read_text().strip()) for c in cpus}
    groups = {}
    for c in cpus:
        groups.setdefault(topology[c], []).append(c)
    threaded = [g for g in groups.values() if len(g) == 2]
    if len(threaded) >= 4*maximum:
        # 本机为大小核混合；两臂各四个同类双线程核心，避免一臂落到小核。
        return [sorted(c for group in threaded[s:4*maximum:maximum] for c in group)
                for s in range(maximum)]
    width = (len(cpus)-4)//maximum
    require(width >= 4, '无法为每台客机分配独立CPU集合及宿主余量')
    return [cpus[4+s*width:4+(s+1)*width] for s in range(maximum)]


def wrapper_source():
    text = old_wrapper()
    start = text.index('env -i PATH=')
    end = text.index("  python3 -c 'import os,resource,sys;resource.setrlimit")
    environment = '\n'.join(s for s in text[start:end].splitlines() if 'YCSB_RANDOM_SEED=' not in s)
    result = (ROOT/'tests/run_dbbench_guest.sh').read_text()
    require(result.count('__DBBENCH_ENV__') == 1, '环境占位符不唯一')
    return result.replace('__DBBENCH_ENV__', environment)


def freeze(output):
    folder = output/'源码'
    if (output/'运行软件身份.json').exists():
        require(digest(folder/'run_dbbench_guest.sh') == __import__('hashlib').sha256(wrapper_source().encode()).hexdigest(),
                '业务脚本变化，不能只恢复核验')
        for name in ('capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py'):
            require(digest(folder/name) == digest(ROOT/'tests'/name), '观测代码改变，拒绝混用原试次')
        update = output/'核验修正_v2'
        update.mkdir(exist_ok=False)
        for source in (ROOT/'analyze_dbbench.py', ROOT/'tests/dbbench_suite.py',
                       ROOT/'tests/run_dbbench_parallel_host.py', ROOT/'tests/test_dbbench.py'):
            shutil.copyfile(source, update/source.name)
        stamp(update/'说明.json', dict(原因='无ZC的完整小测没有评分行，不应强制要求存在评分',
            处理='只在没有选区方案时允许缺少评分；实际存在评分及方案时保持完整重算',
            原分析SHA256=digest(folder/'analyze_dbbench.py'), 新分析SHA256=digest(ROOT/'analyze_dbbench.py'),
            原业务和软件清单不变=True, 已完成业务不重跑=True))
        return
    for source in ROOT.glob('*.py'):
        shutil.copyfile(source, folder/source.name)
    for name in ('dbbench_suite.py', 'capture_dbbench_guest.py', 'capture_ycsb_stream_guest.py',
                 'strict_stats_guest.py', 'run_dbbench_parallel_host.py', 'test_dbbench.py'):
        shutil.copyfile(ROOT/'tests'/name, folder/name)
    (folder/'run_dbbench_guest.sh').write_text(wrapper_source())
    expected = json.loads((output/'同库身份.json').read_text())
    target = '/home/femu/'+output.name.removesuffix('-results')
    for name in ('run_dbbench_guest.sh','capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py'):
        expected[target+'/'+name] = digest(folder/name)
    stamp(output/'运行软件身份.json', expected)
    (folder/'software.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in expected.items()))
    stamp(output/'正式安排.json', dict(负载=CASES, 装载键数=RECORDS, 策略=MODES,
        热点='原生readwhilewriting；偏斜指数范围10；一读线程及一写线程；固定120万读及15万写',
        混合='原生均匀键readrandomwriterandom；95/5为120万次，50/50为30万次',
        小测='每负载两组均8192键/8192业务请求，hot固定2048写；小测全部通过后才正式',
        挂载='装载和业务为两个原生进程，设备NAND和RC连续；两臂使用相同流程，记录重挂载边界',
        核验='全键迭代数量和块CRC，不冒称YCSB逐请求最新值审计',
        保护='512MiB单文件预算、180秒真实请求无进展保护、双盘原保留线；异常冻结证据并正常关机',
        限制='并行CPU集合独立，仍共享内存总线/磁盘；hot原生吞吐为读吞吐，另算全进程总操作吞吐'))


def worker(args, password, output, tag, case, mode, stage, slot, cpus):
    os.sched_setaffinity(0, cpus)
    out = output/tag
    out.mkdir()
    run = DbRun(args, password, out, tag, 8101+slot,
                IMAGE_DIR/(args.label+'-prepare.qcow2'))
    remote = run.target+'/'+tag
    error = None
    try:
        run.record(CPU集合=cpus, 监督工作进程PID=os.getpid())
        run.start()
        payload = output/'源码'
        run.command(run.scp()+[str(payload/n) for n in ('run_dbbench_guest.sh',
            'capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py','software.sha256')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.deployed = True
        free = int(run.ssh("python3 -c 'import os;s=os.statvfs(\"/\");print(s.f_bavail*s.f_frsize)'"))
        require(free >= 6<<30, '客机根盘不足6GiB')
        records, operations = (8192, 8192) if stage == 'smoke' else (RECORDS, CASES[case])
        run.root(f'nohup setsid bash {run.target}/run_dbbench_guest.sh {tag} {mode} {case} '
            f'{records} {operations} < /dev/null > {run.target}/{tag}.启动.log 2>&1 &')
        start = changed = time.monotonic(); previous = None
        while time.monotonic()-start < (1800 if stage == 'smoke' else 14400):
            disk_space()
            free = int(run.ssh("python3 -c 'import os;s=os.statvfs(\"/\");print(s.f_bavail*s.f_frsize)'"))
            require(free >= 6<<30, '运行中客机根盘不足6GiB')
            text = run.root(f'python3 {run.target}/capture_dbbench_guest.py progress {remote}', 35)
            point = json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
            run.record(阶段=tag+'：'+point['阶段'], 最近观测=point,
                资源观测=resource_sample(run.state['当前客机PID']), 客机根盘可用字节=free)
            require(not point['错误'], '出现实际后台或读写错误')
            if point['完成']:
                break
            key = point['阶段'], point['进度']
            if key != previous:
                previous, changed = key, time.monotonic()
            require(time.monotonic()-changed < 180, '180秒真实业务无进展，不用后台维护冒充前进')
            time.sleep(10)
        else:
            raise RuntimeError('超过本轮登记时限')
    except BaseException as failure:
        error = failure
        run.record(原始错误=repr(failure))
        raise
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            run.finish(error)
        except Exception as cleanup:
            if error is None:
                raise
            run.record(收尾另有错误=str(cleanup))
    data = analyze(out/Path(run.target).name/tag, tag, case, mode, records, operations)
    stamp(out/'独立核验.json', data)
    run.record(阶段=tag+'独立验收通过，客机退出', 最近结果={k:data[k] for k in
        ('RR次数','ZC次数','总迁移字节','RR独立改选次数','业务进程吞吐ops每秒')})


def compare(output, stage, case):
    arms = {}
    for mode in MODES:
        tag = stage+'-'+case+'-'+mode
        status = json.loads((output/tag/'阶段归档.json').read_text())
        require(status['完整'] and not status['客机仍存活'] and status['业务成功'], '阶段未归档成功')
        arms[mode] = json.loads((output/tag/'独立核验.json').read_text())
    for key in ('公共参数SHA256','业务参数SHA256','实际读次数','实际写次数'):
        require(arms['space'][key] == arms['model'][key], '配对操作或参数不同：'+key)
    pair = dict(配对验收通过=True, 工作负载=case, 阶段=stage, 模式=arms,
        差值={k:arms['model'][k]-arms['space'][k] for k in
            ('RR次数','ZC次数','总迁移字节','总成功ZoneReset','业务进程吞吐ops每秒')},
        边界='同种子和固定操作数；并发读写交错及后台布局不保证相同，原生基准未作逐请求轨迹审计')
    stamp(output/(stage+'-'+case+'-配对核验.json'), pair)
    return pair


def execute(args, password, output, budget):
    freeze(output)
    maximum = min(6, budget['资源允许并发'])
    cpus = budget['逻辑CPU']
    sets = cpu_sets(cpus, maximum)
    state = dict(监督进程PID=os.getpid(), 阶段='并行真实ZNS小测', 最大并行=maximum,
        RR阈值=1024, GC启动百分比=20, 已完成=[], 运行任务={}, 共享宿主=True, CPU集合=sets)
    running = {}
    def record():
        state['更新时间UTC'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        state['运行任务'] = {str(pid):dict(标签=tag, 槽位=slot) for pid,(tag,slot) in running.items()}
        stamp(output/'状态.json', state)
    try:
        for stage in ('smoke', 'full'):
            queue = []
            for case in CASES:
                for mode in MODES:
                    tag = stage+'-'+case+'-'+mode
                    archived = output/tag/'阶段归档.json'
                    if archived.exists():
                        status = json.loads(archived.read_text())
                        require(status['完整'] and status['业务成功'] and not status['客机仍存活'],
                                '旧阶段业务或收尾失败，不自动重跑：'+tag)
                        records, operations = (8192,8192) if stage == 'smoke' else (RECORDS,CASES[case])
                        data = analyze(output/tag/args.label/tag,tag,case,mode,records,operations)
                        stamp(output/tag/'独立核验.json',data)
                        state['已完成'].append(tag)
                    else:
                        require(not (output/tag).exists(), '存在未完整归档目录，不自动覆盖或重跑')
                        queue.append((case,mode))
                if all(stage+'-'+case+'-'+m in state['已完成'] for m in MODES):
                    compare(output,stage,case)
            record()
            while queue or running:
                while queue and len(running) < maximum:
                    disk_space()
                    slot = next(s for s in range(maximum) if s not in [v[1] for v in running.values()])
                    case, mode = queue.pop(0)
                    tag = stage+'-'+case+'-'+mode
                    pid = os.fork()
                    if pid == 0:
                        try:
                            worker(args, password, output, tag, case, mode, stage, slot,
                                   sets[slot])
                        except BaseException as failure:
                            print(tag, '原始错误：', repr(failure), flush=True)
                            os._exit(1)
                        os._exit(0)
                    running[pid] = tag, slot
                    record()
                for pid in list(running):
                    ended, status = os.waitpid(pid, os.WNOHANG)
                    if ended:
                        tag, slot = running.pop(pid)
                        require(os.waitstatus_to_exitcode(status) == 0, '任务失败，停止后续并收尾：'+tag)
                        state['已完成'].append(tag)
                        parts = tag.split('-'); case = parts[1]
                        if all(stage+'-'+case+'-'+m in state['已完成'] for m in MODES):
                            pair = compare(output, stage, case)
                            state['最近差值'] = pair['差值']
                            if stage == 'full':
                                from plot_dbbench import render
                                render(output)
                        record()
                time.sleep(5)
            state['阶段'] = stage+'全部配对验收通过，全部客机退出'
            record()
        state['阶段'] = 'db_bench正式实验及图表完成，全部客机退出'
        record()
    except BaseException as failure:
        state['错误'] = repr(failure)
        state['阶段'] = '停止排队，正在归档及正常关闭已启动客机'
        record()
        raise
    finally:
        # 只向本协调器刚fork且尚未回收的工作进程发正常停止请求。
        for pid in running:
            if Path('/proc', str(pid)).exists():
                require(Path('/proc', str(pid)).stat().st_uid == os.getuid(), '工作PID归属不符')
                os.kill(pid, signal.SIGTERM)
        while running:
            for pid in list(running):
                ended, _ = os.waitpid(pid, os.WNOHANG)
                if ended:
                    running.pop(pid)
            record()
            if running:
                time.sleep(5)
        if '错误' in state:
            state['阶段'] = '已停止；工作进程收尾结束，具体客机退出状态见各阶段归档'
        record()
