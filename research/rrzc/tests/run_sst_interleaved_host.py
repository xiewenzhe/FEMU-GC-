"""冻结采样对象，先Greedy重复；有在线RR成本证据才继续模型对照。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
from run_pool_dbbench_host import (BASELINE, DONOR, BINARY, CalibrationRun, PREVIOUS, PARENT, ROOT,
    IMAGE_DIR, admission, check_baseline_budget, detach_supervisor, freeze, freeze_bundle,
    preflight, profile8, stamp, wait_stage, stop_business, preserve_unverified_image, digest,
    require, collect, SEMANTIC, initial, request_comparison)
from sampling_scope_bundle import closed
from calibration_source import once
from analyze_pool_dbbench import arm, repeat_guard
from analyze_sst_interleaved import timing
from analyze_sampling_scope import suggestion_difference

PRIOR = IMAGE_DIR / 'joint-controlled-sst-sampling-v1-20261030-results'


def sampling_guard(log, report):
    require('全块采样范围：未满区=1，代表诊断=1' in log, '采样配置不符')
    # GC未选区或没有合格候选时，不要求凭空产生候选代表块日志。
    count=sum(len(r['候选']) for r in report['逐轮选择'])
    require(not count or '全块代表门控：' in log, '已有候选但代表块诊断缺失')


def freeze_sampling(output):
    guest = closed(PRIOR)
    proof = json.loads((PRIOR / 'Greedy重复核验.json').read_text())
    build = json.loads((guest / '生命周期构建核验.json').read_text())
    require(proof['通过'] and build['通过'] and build['模型与原库未修改'], '采样来源未验收')
    require(digest(guest / 'fs_colocation.o') == build['冻结ZenFS对象SHA256'], '采样对象不同')
    files = {'fs_colocation.o': guest / 'fs_colocation.o'}
    for p in (guest / '协同池副本').rglob('*'):
        if p.is_file():
            require(not p.is_symlink() and p.suffix in ('.cc', '.h', '.inc'), '冻结树含非法文件')
            files[str(p.relative_to(guest))] = p
    payload = output / '源码/冻结对象.tar.gz'
    with tarfile.open(payload, 'x:gz') as tar:
        for name, path in files.items(): tar.add(path, arcname=name, recursive=False)
    fs = guest / '协同池副本/plugin/zenfs/fs'
    old = json.loads((PRIOR / '源码/冻结对象清单.json').read_text())
    stamp(output / '源码/冻结对象清单.json', dict(来源=str(PRIOR), 对象SHA256=digest(guest / 'fs_colocation.o'),
        原库SHA256=old['原库SHA256'], 文件={n: digest(p) for n, p in files.items()},
        压缩包SHA256=digest(payload), 禁止旧对象=[p.stem+'.o' for p in fs.glob('*.cc')]))
    scorer = output / '源码/宿主评分核'; shutil.copytree(PRIOR / '源码/宿主评分核', scorer)
    identity = json.loads((scorer / '编译身份.json').read_text())
    require(digest(scorer / '维护核重算') == identity['二进制SHA256'], '评分程序不同')
    for n, sha in identity['头文件'].items():
        require(digest(scorer / n) == digest(fs / n) == digest(ROOT / 'model' / n) == sha, '模型头不同：'+n)
    return scorer / '维护核重算'


def execute(args, password, output, scorer):
    control = getattr(args, 'flush_control', None)
    target = getattr(args, 'target_compare', False)
    pair = getattr(args, 'pair_admission', False) or target
    flush_enabled = args.flush_w and control != 'off'
    run = CalibrationRun(args, password, output, 'calibration', 8101, PARENT, retain_image=False)
    run.fresh_image = True; run.ftl_audit_binary = str(BINARY); run.ftl_audit_sha256 = digest(BINARY)
    run.profile = profile8(); run.args_reuse_image = IMAGE_DIR / (args.label+'-calibration.qcow2')
    run.state.update(边界='单前台持续交错，先Greedy重复，非正常并发性能', GC启用=True, 持续交错=True,
                     业务Flush来源W=flush_enabled, Flush来源同程序对照=control,
                     配对准入对照=pair and not target, 双目标比较对照=target)
    error = None
    try:
        run.start(); run.ssh(f'test ! -e {run.target} && mkdir {run.target}'); run.deployed = True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='逐字复用已验收Flush程序，仅切来源开关' if control else
                   '独立重编五单元与Flush来源，原库和请求体不改' if args.flush_w else
                   '恢复冻结软件，仅编译交错前端与最小校验')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}', 180)
        builder=('reuse_flush_origin_guest.py' if control else
                 'build_flush_origin_guest.py' if args.flush_w else 'build_sst_interleaved_guest.py')
        run.ssh(f'python3 {run.target}/{builder} {run.target}', 900)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/生命周期构建核验.json', str(output/'生命周期构建核验.json')])
        build = json.loads((output/'生命周期构建核验.json').read_text())
        require(build['交错小测逐条请求通过'] and
                (build['ZenFS完整五单元重编'] if args.flush_w else build['ZenFS未重编']), '实际交错小测失败')
        if args.flush_w:
            run.root(f'bash {run.target}/run_flush_probe_guest.sh', 30)
            require('Flush来源元数据通过' in run.ssh(f'cat {run.target}/来源元数据探针.log'), '实际元数据小测失败')
        run.record(阶段='共同装载与窗口0标签核验')
        wait_stage(run, 'source', 'run_pool_source_guest.sh', 1800, mode='greedy')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/真实SST布局核验.json', str(output/'真实SST布局核验.json')])
        for tag in (('p1', 'p2', 'm1', 'm2') if args.model_pair or pair else ('p1', 'p2')):
            if tag == 'm1' and args.model_pair:
                costs = json.loads((output/'p1-核验.json').read_text())['逐轮选择']
                positive = sum(c['风险可知'] and c['RR复制'] > 0 for r in costs for c in r['候选'])
                stamp(output/'在线机会准入.json', dict(正RR预计成本快照数=positive, 继续模型=positive>0,
                    规则='两次Greedy通过且至少一个候选有在线正RR成本；零机会不靠事后RR填正成本'))
                if not positive:
                    run.record(模型未执行原因='Greedy重复通过，但没有在线正RR成本候选，先分析边界'); break
            greedy = pair or tag.startswith('p'); run.record(阶段=tag+'持续交错')
            wrapper=(('run_target_off_guest.sh' if tag.startswith('p') else 'run_target_on_guest.sh')
                     if target else ('run_pair_off_guest.sh' if tag.startswith('p') else 'run_pair_on_guest.sh')) if pair else 'run_pool_business_guest.sh'
            wait_stage(run, tag, wrapper, 1800, mode='greedy' if greedy else 'blocks')
            path = collect(run, tag)
            report = arm(path, greedy, scorer, reads=393216, writes=737280, timing_reader=timing)
            log = (path/'run.zenfs.log').read_text()
            sampling_guard(log,report)
            if target:
                enforce=int(not tag.startswith('p'))
                require(build.get('双目标比较版本')==1 and
                        f'双目标比较配置：执行={enforce}，采证=1' in log and
                        '配对准入配置：启用=0，采证=1' in log,'双目标身份、观测或旧拒绝开关不符')
                require('双目标比较决定：' in log,'缺少实际H迁移比较')
            elif pair:
                enforce=int(not tag.startswith('p'))
                require(f'配对准入配置：启用={enforce}，采证=1' in log, '配对执行开关或共同采证不同')
                require('配对准入决定：' in log, '缺少真实目标准入记录')
            if args.flush_w:
                if flush_enabled:
                    require('Flush来源W标记：' in (path/'run.log').read_text(), '真实Flush没有生成W标签')
                    require('Flush来源挂载文件：' in (path/'verify.zenfs.log').read_text(), '来源未通过真实重挂载恢复')
                else:
                    from flush_control_bundle import disabled_guard
                    disabled_guard(path)
            if tag != 'p1':
                before = output/'逐臂采证/p1'
                require(all(v['相同'] for k, v in initial(before, path).items() if k in SEMANTIC), '对照起点不同')
                require(request_comparison(before, path)['逻辑提交相同'], '对照请求不同')
            stamp(output/(tag+'-核验.json'), report)
            run.record(已完成=[*run.state.get('已完成', []), tag])
            if tag in ('p2', 'm2'):
                first='m1' if tag=='m2' else 'p1'
                proof = repeat_guard(output/'逐臂采证'/first, path, request_count=1130496)
                prefix=('原放置Greedy' if tag=='p2' else '双目标Greedy' if target else '准入Greedy') if pair else ('Greedy' if greedy else '模型')
                stamp(output/(prefix+'重复核验.json'), proof)
                require(proof['通过'], '同策略重复失败，保留首差停止')
    except BaseException as failed:
        error = failed; run.record(原始错误=repr(failed))
        if run.deployed:
            try: stop_business(run)
            except Exception as stopped: run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        try: run.finish(error)
        except Exception as cleanup:
            retained = preserve_unverified_image(run, cleanup)
            if error is None and not retained: raise
            if error is not None: run.record(收尾另有错误=repr(cleanup))
    summary = {}
    for tag in run.state['已完成']:
        r = json.loads((output/(tag+'-核验.json')).read_text())
        summary[tag] = dict(维护=r['维护'], 计时=r['计时'], API延迟=r['API延迟'],
            末端空间={k:v for k,v in r['末端空间'].items() if k!='Zone'},
            正RR成本候选数=sum(c['RR复制']>0 for z in r['逐轮选择'] for c in z['候选']),
            实际改选次数=r['改选次数'],
            影子建议差异数=sum(suggestion_difference(z['字段']) for z in r['逐轮选择']))
    stamp(output/'持续交错结果.json', dict(各臂=summary, 无正式收益结论=True,
        边界='新请求交错及受控维护边界；有限重复不证明正常并发收益。候选随后RR不等于可避免复制。'))
    run.record(阶段='持续交错验收及归档完成，客机退出', 完整核验通过=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label', default='joint-controlled-sst-interleaved-v1-20261030')
    p.add_argument('--check', action='store_true'); p.add_argument('--detach', action='store_true')
    p.add_argument('--model-pair', action='store_true')
    p.add_argument('--pair-admission', action='store_true', help='同一新程序、统一观测的准入关闭/开启，各Greedy重复两次')
    p.add_argument('--target-compare', action='store_true',help='先验证必要H迁移的双目标取得/释放，两组均关闭旧配对拒绝')
    p.add_argument('--flush-w', action='store_true', help='只保留初始H，W来自业务真实Flush；新元数据只用于独立副本')
    p.add_argument('--flush-control', choices=('off', 'on'),
                   help='逐字复用已验收Flush程序与H-only起点，只切业务来源开关；各自独立重复Greedy')
    args = p.parse_args(); require(re.fullmatch('joint-controlled-[a-z0-9-]+', args.label), '标签非法')
    require(not args.flush_control or (not args.flush_w and not args.model_pair),
            '来源开关对照独立使用，不能混入重编译或模型对照')
    require(not args.pair_admission or (not args.flush_control and not args.model_pair and not args.flush_w),
            '配对准入对照不能混入Flush来源关闭或选择器变化')
    require(not args.target_compare or (not args.pair_admission and not args.flush_control and
            not args.model_pair and not args.flush_w),'双目标对照独立使用，不混入其他选择规则')
    if args.pair_admission: args.flush_w = True
    if args.target_compare: args.flush_w = True
    if args.flush_control: args.flush_w = True
    admission(BASELINE); guest = closed(PRIOR)
    require(json.loads((PRIOR/'Greedy重复核验.json').read_text())['通过'], '来源未重复通过')
    budget = preflight(); check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30, '资源保护线不足')
    output = IMAGE_DIR/(args.label+'-results'); require(not output.exists(), '不覆盖结果')
    budget.update(新增磁盘预算字节=16<<30, 单台内存预算字节=14<<30,
                  说明=('单台串行，逐字复用已验收Flush程序，仅切业务来源开关；原200GiB上限与保护线不变'
                        if args.flush_control else
                        '单台串行，同ABI重编五个ZenFS单元与Flush来源，原库/模型不改；原200GiB上限与保护线不变'
                        if args.flush_w else '单台串行，重编交错前端并链接冻结ZenFS；完整采证；原200GiB上限与保护线不变'))
    if args.check: print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('持续交错实验认证（仅内存）：'); output.mkdir()
    freeze(output, restore_fix=True, reuse_build=PREVIOUS)
    reused = json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output, PREVIOUS, DONOR, reused, metadata_reference=False)
    names=('restore_calibration_guest.py','build_pool_dbbench_guest.py','build_all_blocks_guest.py',
        'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py','dbbench_space_probe.cc',
        'pool_dbbench_guest.py','logical_input_guest.py','calibration_guest.py','strict_stats_guest.py',
        'metadata_start_guest.py','sst_lifetime_source.py','rrzc_sst_lifetime.h','sst_interleaved_source.py',
        'build_sst_interleaved_guest.py','run_sst_interleaved_host.py')
    for name in names: shutil.copyfile(ROOT/'tests'/name, output/'源码'/name)
    if args.flush_w:
        for name in ('build_flush_origin_guest.py','flush_origin_source.py','flush_metadata_probe.cc',
                     'pool_flush_guest.py','run_flush_probe_guest.sh'):
            shutil.copyfile(ROOT/'tests'/name, output/'源码'/name)
        shutil.copyfile(ROOT/'model/rrzc_flush_origin.h', output/'源码/rrzc_flush_origin.h')
    if args.pair_admission or args.target_compare:
        shutil.copyfile(ROOT/'tests/pair_admission_source.py',output/'源码/pair_admission_source.py')
        for name in ('rrzc_pair_admission.h','rrzc_pair_zenfs.inc'):
            shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
        (output/'源码/启用配对准入构建').write_text('独立副本，元数据不扩展，全部五单元同ABI重编\n')
    if args.target_compare:
        shutil.copyfile(ROOT/'tests/target_compare_source.py',output/'源码/target_compare_source.py')
        for name in ('rrzc_target_compare.h','rrzc_target_compare_zenfs.inc'):
            shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
        (output/'源码/启用双目标比较构建').write_text('先验证必要H迁移的取得/释放；不更改新文件或选区规则\n')
    if args.flush_control:
        for name in ('flush_control_bundle.py', 'reuse_flush_origin_guest.py'):
            shutil.copyfile(ROOT/'tests'/name, output/'源码'/name)
    native = DONOR/DONOR.name.removesuffix('-results')
    for name in ('rrzc_tls_seed.h','rrzc_front_order.h','rrzc_io_trace.h','rrzc_boundary_sync.h',
        '校准-db_bench_tool.cc','诊断-flush_job.cc','诊断-sync_point.cc','诊断-sync_point_impl.cc'):
        shutil.copyfile(native/name, output/'源码'/name)
    for name in ('受控标签.txt','受控标签来源.json','run_pool_source_guest.sh','run_pool_business_guest.sh'):
        shutil.copyfile(PRIOR/'源码'/name, output/'源码'/name)
    if args.flush_w:
        info=output/'源码/受控标签来源.json'; tags=json.loads(info.read_text())
        tags['标签']={n:t for n,t in tags['标签'].items() if t=='H'}
        require(len(tags['标签'])==2, '初始H身份不同')
        tags.update(标签范围='业务Flush', W规则='仅run进程真实Flush上下文中创建的新SST；不读取未来删除结果')
        stamp(info,tags)
        (output/'源码/受控标签.txt').write_text(''.join(n+' H\n' for n in sorted(tags['标签'])))
        path=output/'源码/run_pool_source_guest.sh'
        path.write_text(once(path.read_text(),'pool_dbbench_guest.py','pool_flush_guest.py'))
    path = output/'源码/run_pool_business_guest.sh'
    path.write_text(once(path.read_text(),'RRZC_SST_LIFETIME_PERSISTENT_H=1',
                         'RRZC_SST_LIFETIME_INTERLEAVED=1 RRZC_SST_LIFETIME_PERSISTENT_H=1'))
    if args.flush_w:
        from flush_origin_source import business_wrapper
        path.write_text(business_wrapper(path.read_text()))
        if args.flush_control == 'off':
            path.write_text(once(path.read_text(), 'flush_w=1;hotspot=1;phase run',
                                 'flush_w=0;hotspot=1;phase run'))
    if args.pair_admission or args.target_compare:
        from pair_admission_source import business_wrapper as pair_wrapper, arm_wrapper
        path.write_text(pair_wrapper(path.read_text()))
        if args.target_compare:
            from target_compare_source import business_wrapper as target_wrapper, arm_wrapper
            path.write_text(target_wrapper(path.read_text()))
        for mode,value in (('off',0),('on',1)):
            script=output/'源码'/f'run_{"target" if args.target_compare else "pair"}_{mode}_guest.sh'
            script.write_text(arm_wrapper(value))
            subprocess.run(['bash','-n',str(script)],check=True)
    for name in ('run_pool_source_guest.sh','run_pool_business_guest.sh'):
        subprocess.run(['bash','-n',str(output/'源码'/name)],check=True)
    (output/'源码/启用本轮元数据参考').write_text('本轮p1真实参考，p2及模型严格核验\n')
    (output/'源码/元数据参考首臂.txt').write_text('p1\n')
    if args.flush_control:
        from flush_control_bundle import freeze as freeze_control
        scorer = freeze_control(output)
    else:
        scorer = freeze_sampling(output)
    if args.flush_w and not args.flush_control:
        old=closed(IMAGE_DIR/'joint-controlled-sst-interleaved-v1-20261030-results')
        build=json.loads((old/'生命周期构建核验.json').read_text())
        require(build['交错小测逐条请求通过'], '请求来源未验收')
        stamp(output/'源码/Flush来源构建计划.json', dict(请求源码SHA256=build['新客户端源码SHA256'],
            原对象SHA256=build['冻结ZenFS对象SHA256'],来源=str(old),新来源元数据版本=11))
    stamp(output/'预注册计划.json', dict(平台=profile8(), 固定K=1, RR阈值=1024, GC百分比=20,
        固定H=True, 标签范围=('初始仅H，业务Flush来源关闭' if args.flush_control == 'off' else
                             '初始H及业务Flush来源W' if args.flush_w else '窗口0全部H/W，其他普通分配'),
        Flush来源W=args.flush_w and args.flush_control != 'off', Flush来源同程序对照=args.flush_control,
        配对准入=args.pair_admission, 双目标比较=args.target_compare,
        双目标边界='必要H迁移、真实部分区备选、非H更少且剩余不更大才替代；当前轮为分配功能验证，不是ZC模型收益实验' if args.target_compare else None,
        配对窗口纳秒=10000000000 if args.pair_admission else None,
        配对伴随复制预算字节=0 if args.pair_admission else None,
        请求读=393216, 请求写=737280,
        交错='每写后补齐floor(累计写数*32768/61440)个累计读，H键连续循环；每pass重置读数',
        Flush='保留每2048写Flush及等待，每pass结束再Flush；检查点等待正常后台，不额外强制Flush',
        检查点='每pass四个H8192片段终点加一个W完成，共60；时点变化须重新验收，不冒称原检查交错',
        顺序=(['共同装载','原放置Greedy-p1','原放置Greedy-p2','准入Greedy-m1','准入Greedy-m2']
              if args.pair_admission else ['共同装载','原放置Greedy-p1','原放置Greedy-p2','双目标Greedy-m1','双目标Greedy-m2']
              if args.target_compare else ['共同装载','p1','p2','有在线机会才m1/m2'] if args.model_pair else ['共同装载','p1','p2']),
        缓存='Block/Row/Persistent关闭，WAL关闭，DirectIO，MemTable保留',
        单台串行=True, 资源=budget, 冻结维护来源=str(PRIOR),
        目的=('先验收必要H迁移的双目标比较、目标取得释放及重复账目；本轮不作为ZC模型收益实验'
              if args.target_compare else '先核验候选时在线增长，再评估总复制与空间；不按事后RR挑选改选区')))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output): return
    try: execute(args, password, output, scorer)
    finally: password = None


if __name__ == '__main__': main()
