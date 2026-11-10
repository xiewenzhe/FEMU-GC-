"""第六阶段独立五单元构建与设备功能验收；继承归档/镜像生命周期保护。"""
import argparse
import getpass
import json
import shutil
import signal
import shlex
import tarfile
from pathlib import Path
from run_placement_integration_host import CalibrationRun,profile8,BINARY,PARENT,configure,compact,stamp,digest,require,composition_audit,preflight,check_baseline_budget,IMAGE_DIR,ROOT,detach_supervisor
from placement_wmig_source import prepare as prepare_source,MODE


def prepare(output):
    copy=output/'独立构建副本';prepare_source(copy)
    source=copy/'源码'
    # 生命周期入口期待源码目录；复制为软链接只引用本轮已生成的新副本。
    (output/'源码').symlink_to(source,target_is_directory=True)
    payload=output/'传输.tar.gz'
    with tarfile.open(payload,'x:gz') as tar:
        for p in source.rglob('*'):
            if p.is_file():tar.add(p,arcname=str(p.relative_to(source)),recursive=False)
    return payload

def execute(args,password,output,payload):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.profile=profile8();run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.state.update(边界='第三阶段独立完整构建及功能小测，不是消融收益实验',GC启用=False)
    configure(run,ftl_audit=False)
    policy=run.state['结果保存策略'];policy.update(本轮归档含输入字节=False,
        输入说明='功能载荷由归档程序与固定CLI生成；未保存完整NAND或逐请求业务输入检查点')
    stamp(output/'结果保存策略.json',policy);run.record(结果保存策略=policy)
    # 不归档中间对象和传输包；保留程序、命令、源码身份及测试证据。
    run.archive_exclusions+=('传输.tar.gz','计时对象/*.o','计时客户端.o','计时ZenFS.o',
        '诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o','计时链接映射.txt')
    error=None
    try:
        run.start();run.ssh(f'mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(payload),f'femu@127.0.0.1:{run.target}/传输.tar.gz'])
        require(run.ssh(f'sha256sum {run.target}/传输.tar.gz').split()[0]==digest(payload),'传输内容变化')
        run.ssh(f'tar -xzf {run.target}/传输.tar.gz -C {run.target}')
        if not args.reuse_program:
            run.record(阶段='完整重编五单元、Flush支持及客户端')
            run.ssh(f'python3 {run.target}/build_placement_integrated_guest.py {run.target}',900)
        else:
            run.record(阶段='核验并复用同一新程序，客机及内存NAND独立新建')
            remote=json.loads(run.ssh(f'cat {run.target}/传输身份.json'))
            for name,h in remote.items():require(run.ssh(f'sha256sum {run.target}/{name}').split()[0]==h,'客机部署身份不符')
            run.ssh(f'test -x {run.target}/db_bench && test -x {run.target}/placement_probe')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/集成构建核验.json',str(output/'集成构建核验.json')])
        run.record(阶段='G2普通文件系统小测，观测开启及内容校验')
        run.ssh(f'env -i PATH=/usr/bin:/bin LANG=C.UTF-8 ZENFS_RRZC_PLACEMENT_MODE=G2 '
            f'ZENFS_RRZC_PLACEMENT_TRACE={run.target}/普通目录组成.bin '
            f'{run.target}/db_bench --db={run.target}/posix --benchmarks=fillseq,readseq,verifychecksum '
            '--num=1024 --reads=1024 --value_size=4096 --compression_type=none --disable_wal=1 '
            f'--cache_size=0 --row_cache_size=0 --threads=1 --seed=20261023 > {run.target}/普通目录小测.log 2>&1',120)
        if args.mode:
            run.record(阶段=args.mode+'独立空盘RocksDB、纯RR及重挂载功能测试',当前策略=args.mode)
            run.root(f'python3 {run.target}/run_placement_device_guest.py {run.target} {args.mode}',180)
        run.record(阶段='完整集成构建及普通目录小测通过，准备采证关机',已完成=['构建','普通目录小测'])
    except BaseException as e:
        error=e;run.record(原始错误=repr(e));raise
    finally:
        if run.deployed:
            # root设备测试产生0600事件文件；只修复本轮bin证据所有者，保持内容哈希。
            fix="from pathlib import Path;import os,json,hashlib;r=Path("+repr(run.target)+");o=r.stat();rows=[]\n"
            fix+="for p in r.rglob('*.bin'):\n if p.is_file() and not p.is_symlink():\n  h=hashlib.sha256(p.read_bytes()).hexdigest();os.chown(p,o.st_uid,o.st_gid);assert hashlib.sha256(p.read_bytes()).hexdigest()==h;rows.append([str(p.relative_to(r)),h])\n"
            fix+="(r/'采证权限核验.json').write_text(json.dumps(rows,ensure_ascii=False))"
            try:run.root('python3 -c '+shlex.quote(fix),60)
            except Exception as e:run.record(采证权限反馈=repr(e))
        run.finish(error)
        # 成功或失败都在归档退出核验后释放仅用于传输的本轮包。
        saved=run.state.get('镜像处理',{})
        if payload.exists() and saved.get('archive') and digest(Path(saved['archive']))==saved.get('archive_sha256'):
            payload.unlink()
    run.record(结果收尾=compact(output),阶段='集成构建归档并正常退出')
    if payload.exists():payload.unlink()
    if args.mode:
        local=output/run.state['标签']
        require(json.loads((local/'设备功能完成.json').read_text())['通过'],'设备功能未完成')
        reports={name:composition_audit(local/(name+'组成.bin')) for name in ('数据库','RR')}
        for name,report in reports.items():stamp(output/(name+'组成核验.json'),report)
        log=(local/'RR与重挂载小测.log').read_text()
        require('重挂载、完整内容和删除映射验证通过' in log,'重挂载内容未通过')
        rrlog=(local/'RR功能/rr.zenfs.log').read_text()
        require(rrlog.count('RR 完成：')==2 and reports['RR']['有效复制字节']==7<<20,'两次RR及一次W必要迁移复制账目不符')
        require(reports['RR']['原生映射核验次数']>0,'没有原生映射独立核验')
        pool=[t for t in reports['数据库']['目标'] if t['协同目标'] and t['标签']&2]
        if args.mode=='G0':require(not any(t['协同目标'] for r in reports.values() for t in r['目标']),'G0进入协同池')
        else:require(bool(pool),'真实Flush来源没有进入协同池')
        if args.mode=='G1':require(len(reports['RR']['G1拒绝'])>=2,'G1没有拒绝必要迁移H')
        if args.mode=='G2':require(any(t['协同目标'] and t['标签']&1 for t in reports['RR']['目标']),'G2没有接受必要迁移H')
        if args.mode=='G2_WMIG_OFF':
            require(any(t['协同目标'] and t['标签']&1 for t in reports['RR']['目标']),'新模式错误阻止H入池')
            require(not any(t['协同目标'] and t['路径']=='必要迁移' and t['标签']==2 for t in reports['RR']['目标']),'W-only迁移仍然入池')
            require('W-only必要迁移与内容验证通过' in log,'W映射和内容未通过')
            require(sum(t['路径']=='必要迁移' and t['标签']==2 and not t['协同目标'] for t in reports['RR']['目标'])==1,'缺少二进制W普通目标证据')
        stamp(output/'设备集成核验.json',dict(通过=True,模式=args.mode,RR完成=2,功能有效复制字节=7<<20,
            Flush入池=len(pool),H拒绝=len(reports['RR']['G1拒绝']),
            原生映射核验次数=reports['RR']['原生映射核验次数'],真实重挂载内容=True,完整业务实验=False))
        run.record(阶段=args.mode+'设备功能及离线组成核验通过，已归档退出')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-placement-wmig-build-v1-20261109')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    args=p.parse_args();require(args.label.startswith('joint-controlled-placement-wmig-') and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'标签非法')
    resource=preflight();resource['目录已占字节']=check_baseline_budget(8<<30)
    require(resource['data可用字节']>=42<<30 and resource['可用内存字节']>=30<<30,'保持原构建保护线')
    if args.check:print(json.dumps(resource,ensure_ascii=False));return
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧输出')
    password=getpass.getpass('独立构建认证（仅内存）：');output.mkdir();payload=prepare(output)
    args.mode=MODE;args.reuse_program=None
    stamp(output/'预注册计划.json',dict(模式=MODE,独立编译=True,两次RR及一次W必要迁移功能=True,非收益实验=True,资源=resource))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,payload)
    finally:password=None

if __name__=='__main__':main()
