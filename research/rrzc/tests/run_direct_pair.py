"""已编译统一模型同库串行YCSB；各阶段均由精确PID收尾。"""
import json
from pathlib import Path
import time
from run_direct_host import OUTPUT,BUILD,TARGET,LABEL,TESTS,resources
from run_stream_pair import require
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_retention import sha
from analyze_ycsb_backpressure import check_options
from check_ycsb_independent_trace import check
from direct_analysis import audit_run,compare

RESULT='/home/femu/rrzc-ycsb-results/20261017'
PAIR=TARGET+'/pair'
FILES=[TESTS/n for n in ('run_direct_ycsb_guest.sh','check_direct_ycsb_guest.sh','probe_ycsb_progress.py',
    'check_ycsb_independent_trace.py','workload-locality-wide-zns.properties')]
FILES += [TESTS.parent/'rrzc_rr_health.py']


def arm(run,mode,source,expected):
    resources();out=OUTPUT/mode;out.mkdir();run.record(当前模式=mode,阶段=mode+'启动准备')
    try:
        run.start(mode)
        before=run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
        require(hashes(before)==expected,'对照软件与实际构建不同')
        run.ssh(f'test ! -e {PAIR} && test ! -e {RESULT} && mkdir {PAIR}')
        run.command(run.scp()+[*(str(source/p.name) for p in FILES),str(source/'software.sha256'),f'femu@127.0.0.1:{PAIR}/'])
        run.ssh(f'nohup setsid bash {PAIR}/check_direct_ycsb_guest.sh < /dev/null > {PAIR}/small.log 2>&1 &')
        run.record(阶段=mode+'普通目录小回归')
        run.wait_for(PAIR+'/selftest.finished',f'test ! -f {PAIR}/selftest.exit || cat {PAIR}/selftest.exit',300)
        require(run.ssh(f'cat {PAIR}/selftest.exit').strip()=='0','小回归失败，不启动真实业务')
        run.root(f'nohup setsid bash {PAIR}/run_direct_ycsb_guest.sh {mode} < /dev/null > {PAIR}/launcher.log 2>&1 &')
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<3000:
            point=json.loads(run.ssh(f'python3 {PAIR}/probe_ycsb_progress.py'))
            run.record(阶段=mode+'装载、120万次业务和全键校验',最近观测=point)
            with (out/'进度.jsonl').open('a') as f:f.write(json.dumps(point,ensure_ascii=False)+'\n')
            require(not point['错误'],'检测到后台错误，采证关机，不自动重跑')
            if run.ssh(f'test ! -f {PAIR}/run.finished || printf "完成"').strip()=='完成':break
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(time.monotonic()-changed<180,'180秒没有进展')
            time.sleep(15)
        else:raise RuntimeError('本组超过3000秒上限')
    except BaseException as e:
        (out/'运行异常.json').write_text(json.dumps(dict(类型=type(e).__name__,原因=str(e) if isinstance(e,(ValueError,RuntimeError)) else '连接或认证异常，仅记录类型'),ensure_ascii=False,indent=2))
        raise
    finally:
        try:
            p=json.loads(run.ssh(f'python3 {PAIR}/probe_ycsb_progress.py'))
            (out/'收尾现场.json').write_text(json.dumps(p,ensure_ascii=False,indent=2))
            log=p.get('维护日志路径')
            if log:
                require(Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),'现场日志路径不符')
                run.command(run.scp()+[f'femu@127.0.0.1:{log}',str(out/'现场维护.log')],180)
        except Exception as e:
            (out/'采证异常类型.txt').write_text(type(e).__name__)
        finally:run.finish_stage(out,mode,RESULT)
    require((out/LABEL/'pair/run.exit').read_text().strip()=='0','包装器未成功')
    root=out/'20261017'
    from rrzc_rr_health import inspect
    health=inspect((root/'zenfs.run.log').read_text())
    (out/'RR健康验收.json').write_text(json.dumps(health,ensure_ascii=False,indent=2))
    require(health['验收通过'],'维护健康验收失败，详见RR健康验收.json，不生成有效对照')
    r=audit_run(root,mode,health=True)
    for name in ('first','second'):
        check(out/LABEL/f'pair/selftest-{name}/audit.requests',2560,16,8192,update_range=(16,1008))
    r['实际请求分布']=check(root/'audit.requests',655360,4096,1200000,update_range=(4096,258048))
    check_options('\n'.join(p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()))
    require(hashes((root/'software.sha256').read_text())==hashes((source/'software.sha256').read_text()),'运行清单与冻结源不同')
    (out/'独立核验.json').write_text(json.dumps(r,ensure_ascii=False,indent=2));require(r['验收通过'],'完整验收失败')
    run.state['已完成'].append(mode)
    run.record(阶段=mode+'完整验收通过，客机已关闭',RR重置=r['RR重置'],ZC重置=r['ZC重置'],
        模型与空间最少不同=r['模型选择与空间最少不同轮数'],吞吐量=r['吞吐量'])


def run_pair(run):
    require(json.loads((OUTPUT/'smoke/独立核验.json').read_text())['验收通过'],'设备功能短测未通过')
    require(json.loads((OUTPUT/'spare/独立核验.json').read_text())['验收通过'],'真实独占替补回归未通过')
    require(json.loads((OUTPUT/'content/独立核验.json').read_text())['验收通过'],'公共内容隔离及RR健康回归未通过')
    run.parent_image=BUILD;run.state['已完成']=['build','smoke','spare','content']
    source=run.freeze('对照',FILES)
    expected=json.loads((OUTPUT/'同库身份.json').read_text())
    manifest=''.join(f'{v}  {k}\n' for k,v in expected.items())+''.join(sha(source/p.name)+'  '+PAIR+'/'+p.name+'\n' for p in FILES)
    (source/'software.sha256').write_text(manifest)
    plan=dict(模式=['greedy','direct'],父镜像=str(BUILD),
        唯一差异='统一组显式DIRECT=1；均guarded=shadow，统一组不调用Greedy选择和旧建议执行器',
        负载='沿用1GiB范围配置：655360键、4096字节、120万读写各半请求、1000ops/s、单线程、种子20261017、无WAL及无块缓存',
        不变项='8GiB、RR4096、GC20%、192MiB正常回收资格；两组统一启用默认关闭的SST/非SST公共内容隔离',
        健康验收='逐区风险帧；同代际有效数据超阈值等待超过30秒或检查间隔超过30秒拒绝，30秒仅验收门槛不参与维护触发',
        指标='ZC/RR及维护总迁移；设备所有Zone Reset和映射NAND擦除；读/Update均值、p99/p99.9/p99.99；实际Run吞吐',
        边界='保留十秒维护窗口但删除线性折扣、一秒增长否决、Greedy前置及相对预算；最大RC块仍为风险代表，留存历史未知不给抵扣',
        收尾='串行、每组3000秒上限，异常归档并正常关闭，失败停止后续组，不自动重跑')
    (OUTPUT/'对照预注册.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    for mode in ('greedy','direct'):arm(run,mode,source,expected)
    r=compare(OUTPUT);(OUTPUT/'两组对照.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
    run.password=None;run.record(阶段='两组完整完成并核验通过，全部客机已关闭',当前客机PID=None)
