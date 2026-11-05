#!/usr/bin/env python3
"""先Greedy重复控制，再模型→Greedy反向配对；一个临时客机，公式及负载不变。"""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

from run_rr_path_host import RrRun, preflight, IMAGE_DIR, ROOT
from run_all_blocks_followup_host import PARENT, identity, OLD
from run_triggered_gc_host import (FILES, boundary_wrapper, boundary_check, wait_stage,
                                   stop_business, preserve_unverified_image)
from run_joint_host import digest_manifest
from run_ycsb_quick_host import profile8
from run_dbbench_parallel_host import stamp
from triggered_gc_patch import sources
import reserve_restore_patch
from analyze_repeat_order import ARMS
from ycsb_af import require, digest
from reuse_temporary_run import ReuseRun

LABEL = 'joint-repeat-order-v1-20261026'
PRIOR = IMAGE_DIR/'joint-triggered-gc-dbbench-v1-20261026-results'
RECOVERED = IMAGE_DIR/'joint-repeat-order-v1-20261026-results/源码恢复-a2/joint-repeat-evidence-v2-20261026-a2/fs'


def tagged_wrapper(text):
    old = 'result=$target/$mode'
    require(text.count(old) == 1, '实际包装器结果目录锚点不唯一')
    return text.replace(old, '''arm_tag=${RRZC_ARM_TAG:-$mode}
[[ $arm_tag =~ ^[a-z0-9-]+$ ]]
result=$target/$arm_tag''', 1)


def execute(args, password, output):
    # 明确temporary，复用DbRun.start/finish_image的ImageLease，不增加构建检查点。
    cls=ReuseRun if args.reuse_image else RrRun
    run = cls(args, password, output, 'repeat', 8101, PARENT, retain_image=False)
    if args.reuse_image:run.args_reuse_image=args.reuse_image
    run.profile = profile8()
    run.state.update(边界='Greedy两次控制及模型先行的反向配对，不做统计显著性宣称', GC启用=True)
    error = None
    try:
        run.start()
        run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
        run.deployed = True
        expected = identity()
        if not args.reuse_image:
            require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected), 180)) == expected,
                    '父环境身份改变')
        upload = [p for p in (output/'源码').iterdir() if p.is_file()]
        run.command(run.scp()+list(map(str, upload))+[f'femu@127.0.0.1:{run.target}/'])
        if args.reuse_image:
            changed={'plugin/zenfs/fs/'+n for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc','zbd_zenfs.h')}
            preserved={k:v for k,v in expected.items() if k not in changed}
            require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(preserved),180)) == preserved,
                    '复用盘原库或无关模型已变化，拒绝覆盖')
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc','zbd_zenfs.cc','zbd_zenfs.h'):
                run.ssh(f'cp {run.target}/reset-{name} /home/femu/rocksdb/plugin/zenfs/fs/{name}')
            require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180)) == expected,
                    '临时工作盘恢复到已归档基准源失败')
        run.ssh('cd /home/femu/rocksdb/plugin/zenfs && '+
            f'git apply --check {run.target}/triggered-gc.patch && git apply {run.target}/triggered-gc.patch && '
            f'git apply --reverse --check {run.target}/triggered-gc.patch && cp {run.target}/rrzc_triggered_gc.h fs/')
        if args.restore_fix:
            run.ssh('cd /home/femu/rocksdb/plugin/zenfs && '+
                f'git apply --check {run.target}/reserve-restore.patch && git apply {run.target}/reserve-restore.patch && '
                f'git apply --reverse --check {run.target}/reserve-restore.patch')
        run.record(阶段='复用已核验共同修复程序' if args.reuse_build else
                   '统一重编挂载修复，模型公式不变' if args.restore_fix else '编译与上一轮相同的模型对象和客户端')
        builder='reuse_build_guest.py' if args.reuse_build else 'build_restore_guest.py' if args.restore_fix else 'build_triggered_gc_guest.py'
        run.ssh(f'python3 {run.target}/{builder} {run.target}', 600)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/客户端身份.json',str(output/'客户端身份.json')])
        current = json.loads((output/'客户端身份.json').read_text())
        prior = json.loads((PRIOR/'客户端身份.json').read_text())
        same=all(current[k] == prior[k] for k in ('客户端SHA256','全块ZenFS对象SHA256'))
        if not args.restore_fix:
            require(same,'客户端或模型对象与上一轮不同，先保留构建证据，不启动正式实验')
        else:
            stamp(output/'共同修复说明.json',dict(与旧版相同二进制=same,
                原因='已确认挂载时误接管元数据区为迁移预留，Greedy和模型统一修复；RR和victim公式不变',
                比较限制='旧版仅历史参考，四组均用同一修复后程序；模型与前后Greedy比较共用一个模型运行，不算两次独立重复'))
        run.ssh(f'{run.target}/db_bench --db={run.target}/posix --benchmarks=fillseq,readseq,verifychecksum '
                '--num=1024 --value_size=4096 --disable_wal=1 --compression_type=none '+
                f'> {run.target}/普通目录小测.log 2>&1',120)
        if args.restore_fix:
            run.record(阶段='共同修复编译及普通目录校验通过，验证真实设备预留恢复')
            wait_stage(run,'restore-probe','run_restore_probe_guest.sh',120,mode='greedy')
            proof=run.ssh(f'cat {run.target}/restore-probe/run.log')
            require('真实预留恢复回归通过' in proof,'原始设备恢复回归未通过')
            stamp(output/'预留恢复设备核验.json',dict(通过=True,原始输出=proof,
                边界='真实设备写入和Close后模拟恢复文件类别，非SST文件格式，不作性能或收益结论'))
        run.record(阶段='编译及普通目录校验通过，复核零缺口边界')
        wait_stage(run, 'boundary', 'run_boundary_guest.sh', 180)
        for name in ('zenfs.run.log','run.log'):
            run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/boundary/{name}',str(output/('边界-'+name))])
        stamp(output/'边界核验.json',boundary_check((output/'边界-zenfs.run.log').read_text(),
                                                  (output/'边界-run.log').read_text()))
        for tag, mode in ARMS:
            run.record(阶段=tag+'：重新初始化及装载，实际策略='+mode, 当前策略=tag,
                       本轮用途='Greedy重复控制' if tag in ('g1','g2') else '反向顺序配对')
            wait_stage(run, tag, 'run_tagged_guest.sh', 1800, mode=mode)
            run.record(已完成=[*run.state.get('已完成',[]),tag])
        expected = json.loads((output/'源码/expected.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180)) == expected,
                '运行改变了原库或模型')
        run.record(阶段='四组完成，归档并正常关机')
    except BaseException as failure:
        error = failure
        run.record(原始错误=repr(failure))
        if run.deployed:
            try:
                stop_business(run)
            except Exception as stopped:
                run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        try:
            run.finish(error)
        except Exception as cleanup:
            retained = preserve_unverified_image(run,cleanup)
            if error is None and not retained:
                raise
            if error is not None:
                run.record(收尾另有错误=repr(cleanup))
    subprocess.run([sys.executable,str(output/'源码/analyze_repeat_order.py'),str(output/args.label),
                    str(PRIOR),str(output/'分析')],check=True)
    run.record(阶段='四组重复性及反向顺序分析完成，客机已退出',
               镜像状态=run.image_lease.data['status'])


def freeze(output,restore_fix=False,reuse_build=None):
    target = output/'源码'
    target.mkdir()
    for name in (*FILES,'run_repeat_order_host.py','reuse_temporary_run.py'):
        shutil.copyfile(ROOT/'tests'/name,target/name)
    shutil.copyfile(ROOT/'tests/triggered_gc_device.cc',target/'all_blocks_device.cc')
    shutil.copyfile(ROOT/'model/rrzc_triggered_gc.h',target/'rrzc_triggered_gc.h')
    shutil.copyfile(ROOT/'zenfs-triggered-gc.patch',target/'triggered-gc.patch')
    for name in ('analyze_all_blocks_dbbench.py','analyze_repeat_order.py'):
        shutil.copyfile(ROOT/name,target/name)
    base = (target/'run_all_blocks_dbbench_guest.sh').read_text()
    (target/'run_tagged_guest.sh').write_text(tagged_wrapper(base))
    (target/'run_boundary_guest.sh').write_text(boundary_wrapper(base))
    expected = identity()
    changed = sources(*[(OLD/'源码'/('after-'+n)).read_text() for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc')])
    for name,text in zip(('fs_zenfs.cc','rrzc_direct_zenfs.inc'),changed):
        expected['plugin/zenfs/fs/'+name] = hashlib.sha256(text.encode()).hexdigest()
    expected['plugin/zenfs/fs/rrzc_triggered_gc.h'] = digest(target/'rrzc_triggered_gc.h')
    if restore_fix:
        for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
            shutil.copyfile(OLD/'源码'/('after-'+name),target/('reset-'+name))
        for name in ('zbd_zenfs.cc','zbd_zenfs.h'):
            shutil.copyfile(RECOVERED/name,target/('reset-'+name))
        for name in ('build_restore_guest.py','reserve_restore_device.cc'):
            shutil.copyfile(ROOT/'tests'/name,target/name)
        actual=[(RECOVERED/n).read_text() for n in ('fs_zenfs.cc','zbd_zenfs.cc','zbd_zenfs.h')]
        require(actual[0]==changed[0],'恢复源码与当前全块及触发修复不一致')
        patch=reserve_restore_patch.make_patch(*actual)
        (target/'reserve-restore.patch').write_text(patch)
        for name,text in zip(('fs_zenfs.cc','zbd_zenfs.cc','zbd_zenfs.h'),reserve_restore_patch.sources(*actual)):
            (target/('修复后-'+name)).write_text(text)
            expected['plugin/zenfs/fs/'+name]=hashlib.sha256(text.encode()).hexdigest()
        (target/'run_restore_probe_guest.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $(id -u) == 0 && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 ]]
result=$target/restore-probe
[[ ! -e $result && -z $(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]') ]]
mkdir "$result"
trap 'code=$?; echo "$code" > "$result/wrapper.exit"; touch "$result/finished"' EXIT
nvme zns reset-zone /dev/nvme0n1 -a > "$result/初始化Reset.log" 2>&1
printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 ZENFS_MIGRATE_RESERVE=1 \\
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_CONTENT_GUARD=1 ZENFS_MAINT_DIAG=1 \\
  ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_RRZC_SIMPLE_MODE=valid \\
  ZENFS_RRZC_SINGLE_RECOVERY=1 "$target/reserve_restore_device" "$result/zenfs.log" > "$result/run.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$result/zones.final.json"
''')
    if reuse_build:
        state=json.loads((reuse_build/'状态.json').read_text());label=state['标签']
        require(re.fullmatch(r'joint-[a-z0-9-]+',label) is not None,'复用构建目录标签非法')
        names=('fs_all_blocks.o','db_bench','space_probe','all_blocks_device','reserve_restore_device','客户端身份.json')
        hashes={n:digest(reuse_build/label/n) for n in names}
        (target/'复用构建.json').write_text(json.dumps(dict(客机目录='/home/femu/'+label,文件SHA256=hashes),ensure_ascii=False,indent=2))
        shutil.copyfile(ROOT/'tests/reuse_build_guest.py',target/'reuse_build_guest.py')
    (target/'expected.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default=LABEL)
    p.add_argument('--check',action='store_true')
    p.add_argument('--restore-fix',action='store_true')
    p.add_argument('--reuse-image',type=Path)
    p.add_argument('--reuse-build',type=Path)
    args = p.parse_args()
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label) is not None,'非法标签')
    require(not args.reuse_image or args.restore_fix,'只允许修复工作复用已登记临时盘')
    require(not args.reuse_build or (args.reuse_image and args.restore_fix),'复用程序必须同时复用对应临时工作盘')
    budget = preflight()
    require(budget['data可用字节'] >= 28<<30,'24GiB保留、2GiB收尾及2GiB四组预算不足')
    budget['新增磁盘预算字节'] = 2<<30
    require((PRIOR/'分析/独立核验.json').is_file(),'前轮核验数据缺失')
    require(digest(ROOT.parents[1]/'build-femu/qemu-system-x86_64') ==
            json.loads((OLD/'预注册计划.json').read_text())['FEMU_SHA256'],'FEMU版本不同')
    output = IMAGE_DIR/(args.label+'-results')
    require(not output.exists(),'不覆盖旧实验')
    if args.check:
        print(json.dumps(budget,ensure_ascii=False,indent=2))
        return
    password = getpass.getpass('重复性及反向顺序认证（仅内存）：')
    output.mkdir()
    freeze(output,args.restore_fix,args.reuse_build)
    stamp(output/'预注册计划.json',dict(目的='检查Greedy自身波动与顺序效应，不修改模型争取正收益',
        顺序=[dict(目录=tag,实际策略=mode) for tag,mode in ARMS], 资源=budget,
        平台='单台8GiB/64Zone/128MiB、RR1024、GC20%、未锁页',
        负载='与前轮完全相同的24批次，1048576键×4KiB，786432读、589824写，无WAL不限速',
        软件='四组同一二进制；启用共同挂载修复时统一重编ZenFS且旧版只作参考，否则要求与前轮SHA256一致；原库不重编',
        共同挂载修复=args.restore_fix,
        复用已有临时镜像=str(args.reuse_image) if args.reuse_image else None,
        复用已完成构建=str(args.reuse_build) if args.reuse_build else None,
        状态='各组Reset并重新装载，不将qcow2当作NAND或RC快照',
        镜像='只新增一个ImageLease临时镜像，父镜像为旧构建环境，不使用前轮临时镜像作父镜像',
        诊断='同策略重复差异、模型先行/Greedy后行与前轮正向配对对比；两次重复不构成统计显著性',
        收尾='归档、哈希和正常关机；占用权限不足则明确保留，不跳过核验'))
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        execute(args,password,output)
    finally:
        password = None


if __name__ == '__main__':
    main()
