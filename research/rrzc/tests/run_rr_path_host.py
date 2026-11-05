#!/usr/bin/env python3
"""实验3：复用已验收库，关闭GC，受控文件读取验证RR及Reset后新代际。"""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess

from run_gc_path_host import GcRun, ROOT, IMAGE_DIR, SOURCE, PARENT
from run_dbbench_parallel_host import DbRun, stamp
from run_joint_host import digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from run_ycsb_quick_host import disk_space
from ycsb_af import digest, require

LABEL='joint-rr-path-v2-20261025'

GUEST=r'''#!/usr/bin/env bash
# 新建、全空、可销毁模拟盘；不启用GC，开启严格RR及原后台RR检查。
set -euo pipefail
target=$(dirname "$(readlink -f "$0")")
[[ $(id -u) == 0 && $(blockdev --getsize64 /dev/nvme0n1) == 8589934592 && ! -e $target/rr ]]
mkdir "$target/rr" "$target/rr/aux"
trap 'code=$?; echo "$code" > "$target/rr/wrapper.exit"' EXIT
nvme zns report-zones /dev/nvme0n1 -o json > "$target/rr/zones.initial.json"
python3 - "$target/rr/zones.initial.json" <<'PY'
import json,sys
zones=json.load(open(sys.argv[1]))['zone_list']
assert len(zones)==64 and all(z['state']=='EMPTY' and z['wp']==z['slba'] for z in zones)
PY
printf 'mq-deadline\n' > /sys/block/nvme0n1/queue/scheduler
cd /home/femu/rocksdb
# 不传--enable_gc；只格式化本次独立全空FEMU设备。
plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$target/rr/aux" > "$target/rr/mkfs.log" 2>&1
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \
  ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1 ZENFS_MAINT_DIAG=1 ZENFS_ALLOC_DIAG=1 \
  ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 ZENFS_RRZC_CONTENT_GUARD=1 \
  ZENFS_RRZC_MIGRATION_SPARE=1 ZENFS_RRZC_EMPTY_RESERVE=1 ZENFS_RRZC_RESERVE_TARGET=1 \
  ZENFS_RRZC_SINGLE_RECOVERY=1 ZENFS_RR_FAIR_QUEUE=1 \
  ZENFS_RRZC_SIMPLE_MODE=valid ZENFS_RRZC_RECLAIM_BYTES=0 ZENFS_RRZC_V1_SHADOW=1 \
  "$target/rr_path_device" "$target/rr" > "$target/rr/run.log" 2>&1
nvme zns report-zones /dev/nvme0n1 -o json > "$target/rr/zones.final.json"
'''


class RrRun(GcRun):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state.update(边界='RR路径功能实验；GC关闭、RR1024、单台串行，不作性能收益对照',
            设备严格RR=True,主机同步RR=True,后台RR=True,GC启用=False)

    def command(self,argv,timeout=60):
        # 沿用已验证的严格RR启动参数；不继承纯GC入口关闭严格模式的替换。
        return DbRun.command(self,argv,timeout)


def preflight():
    require(PARENT.is_file() and not running_parent(PARENT),'软件父镜像不可用')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'已有本项目客机，不叠加')
    with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',8101))!=0,'8101已占用')
    disk_space()
    memory=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    available=int(memory['MemAvailable'].split()[0])*1024
    disk=os.statvfs(IMAGE_DIR);free=disk.f_bavail*disk.f_frsize
    # 无静态库重编译、无数据库装载；客户端去调试符号，单独登记1GiB新增磁盘预算。
    # 保留24GiB原保护线及2GiB运行收尾空间，不借用或降低原保护额度。
    require(available>=28<<30,'新客机及16GiB内存保留不足')
    require(free>=27<<30,'24GiB保留、2GiB收尾及1GiB小工具预算不足')
    return dict(可用内存字节=available,data可用字节=free,新增磁盘预算字节=1<<30,
        data保留字节=24<<30,运行收尾字节=2<<30,单台内存预算字节=12<<30,
        说明='只链接去调试符号的小型功能工具，复用库，不使用db_bench重建静态库的6GiB预算')


def analyze(root):
    root=Path(root)
    with (root/'块轨迹.csv').open() as stream:rows=list(csv.DictReader(stream))
    with (root/'计数账目.csv').open() as stream:counts=list(csv.DictReader(stream))
    log=(root/'rr.zenfs.log').read_text();run=(root/'run.log').read_text()
    require('启动 Zone 维护线程：GC=0，主机端 RR=1' in log,'GC和后台RR配置不符')
    require('ZC 统计：' not in log and 'GC路径选中：' not in log,'纯RR实验发生ZC')
    require(log.count('RR 完成：')==2 and log.count('RR 统计：')==2,'RR完成次数不等于两次')
    require('纯RR路径验证通过' in run,'受控程序没有通过完整内容及计数验证')
    require('最大RC=1024，阈值=1024，达阈值块=1' in log,'缺少真实1024阈值帧')
    stages={r['阶段']:r for r in rows if r['被观察Zone']==r['当前映射Zone']}
    source=stages['装载后']['当前映射Zone'];target=stages['首次RR后']['当前映射Zone']
    require(source!=target,'第一次迁移没有改变映射')
    identity=('设备代际','最大块编号','Channel','LUN','Plane','Block')
    original=stages['装载后']
    for n in (1,128,512,1023):
        r=stages['读'+str(n)]
        require(r['当前映射Zone']==source and all(r[k]==original[k] for k in identity) and int(r['RC'])==n,
            '阈值前计数不是同一块')
    for stage,zone,previous in (('首次RR后',source,original),('第二次RR后',target,stages['首次RR后'])):
        old=next(r for r in rows if r['阶段']==stage and r['被观察Zone']==zone)
        require(int(old['设备代际'])==int(previous['设备代际'])+1 and
            int(old['主机代际'])==int(previous['主机代际'])+1 and
            all(int(old[k])==0 for k in ('RC','十秒增量','三十秒增量','达阈值块数','读数总和','主机有效字节')) and
            old['设备状态码']=='1' and old['设备WP']==zone,'Reset后的旧区状态、读历史或阈值标记未清除')
    for stage,rc in (('首次RR后',1),('新块追加32读',33),('空闲12秒后',33),('第二次阈值前',1023),('第二次RR后',1)):
        require(int(stages[stage]['RC'])==rc,'迁移后重新计数或空闲观察不符')
    accounting={r['阶段']:r for r in counts}
    for stage,retries in (('首次RR后',1),('第二次RR后',2)):
        c=accounting[stage];ordinary=int(c['计入RC读取'])-int(c['其他块搬迁计数读取'])
        require(ordinary-int(stages[stage]['累计首页读取'])==retries,
            '真实普通NAND读取未能解释每次维护后的请求重读')
    target_before=next(r for r in rows if r['阶段']=='装载后' and r['被观察Zone']==target)
    require(int(target_before['RC'])==0,'首次目标原来已有读历史')
    require(all(int(c['历史最高RC'])<=1024 for c in counts),'RC超过严格阈值')
    require(int(counts[-1]['阈值触发事件'])-int(counts[0]['阈值触发事件'])==2,'独立阈值事件数不符')
    require(int(counts[-1]['自身刷新豁免读取'])>int(counts[0]['自身刷新豁免读取']) and
        int(counts[-1]['其他块搬迁计数读取'])>int(counts[0]['其他块搬迁计数读取']),
        '未验证自身触发块豁免及其他块搬迁照常计数')
    # 复制日志只确认数据复制，之后还必须有成功的映射提交和源Reset。
    from gc_path_trace import fields
    events=log.splitlines();copies=[i for i,s in enumerate(events) if 'V1后续迁移复制：' in s]
    require(len(copies)==2,'缺少两次真实复制记录')
    for i in copies:
        c=fields(events[i],'V1后续迁移复制：');require(c['复制成功']=='1','复制失败')
        submits=[j for j in range(i+1,len(events)) if 'V1后续映射提交：' in events[j] and
            fields(events[j],'V1后续映射提交：')['文件编号']==c['文件编号'] and
            fields(events[j],'V1后续映射提交：')['成功']=='1']
        resets=[j for j in range(i+1,len(events)) if 'Zone 重置诊断：Zone='+c['源Zone']+'，状态=成功' in events[j]]
        require(submits and resets and min(submits)<min(resets),'没有先提交映射再Reset的证据')
    return dict(通过=True,RR次数=2,ZC次数=0,阶段当前映射=list(stages.values()),
        旧源Reset后读数与阈值标记清零=True,空闲12秒无重复=True,两次阈值均为1024=True,
        内容校验字节=3<<20,计数账目=counts,复制映射Reset顺序通过=True,
        边界='受控ZenFS文件IO；不是RocksDB真实SST、YCSB或性能对照；最大RC只用于单一受控热块身份')


def execute(args,password,output):
    run=RrRun(args,password,output,'rr',8101,PARENT);error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        query='cd /home/femu/rocksdb && sha256sum '+' '.join(expected)
        require(digest_manifest(run.ssh(query,180))==expected,'父库、模型或接线身份漂移')
        run.command(run.scp()+[str(output/'源码'/n) for n in ('rr_path_device.cc','run_rr_path_guest.sh')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='只链接受控RR客户端，不重新编译库')
        run.ssh('cd /home/femu/rocksdb && '
            'g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude '
            f'{run.target}/rr_path_device.cc librocksdb_debug.a -Wl,--strip-debug '
            '-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt '
            f'-o {run.target}/rr_path_device > {run.target}/编译.log 2>&1',180)
        require(digest_manifest(run.ssh(query,180))==expected,'链接工具意外改变库或模型')
        run.record(阶段='受控客户端编译通过，GC关闭，开始两次阈值与空闲观察')
        run.root(f'bash {run.target}/run_rr_path_guest.sh > {run.target}/启动.log 2>&1',90)
        run.record(阶段='两次RR及完整内容检查完成，归档后正常关机')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=repr(cleanup))
    data=analyze(output/LABEL/'rr');stamp(output/'路径核验.json',data)
    first=data['阶段当前映射'][0];last=data['阶段当前映射'][-1]
    text=f'''# 实验3：关闭GC后的真实RR路径

本实验通过：8 GiB独立设备、RR1024、超级块GC关闭；后台RR和严格同步RR均开启。复用已验收库，仅链接独立功能程序。文件3 MiB，超过设备2 MiB写缓存条带，使首页自然进入NAND读取计数。

当前映射的逐阶段读数见块轨迹.csv；同一个物理块在同一Reset代际内自然读到1、128、512、1023，第1024次读触发维护。内部RR风险帧记录真实RC1024，之后复制、提交映射、源Reset完成。初始目标块RC为0；当前严格读实现完成维护后重读触发请求，API返回时目标RC为1。设备普通NAND读取比首页API次数多出的读取，与这次重读一一对应，不能误判成旧RC残留。

新位置再追加32次读取，RC从1到33；保持后台RR正常检查，空闲12秒后仍是33，未产生重复RR。再读到该新块RC1023，下一次读触发第二次独立RR。因此第二次来自新位置的1024次真实读取（其中1次是前一触发请求重读），并非旧标志残留。

每次Reset后旧源设备及主机代际增加1，RC总和、近期增量、达阈值块数均为0，源状态Empty、WP回到起点。C1阈值标记由当前RC实时计算，不是需要单独手动清除的持久化设备pending布尔标志。主机公平队列的代际观测继续保留。

总RR为2，ZC为0；最高RC为1024。完整3 MiB内容校验通过；自身到期块刷新读取豁免、其他块搬迁读取继续计数，都由设备C3账目验证。完整内容校验也会增加新块RC，不能把校验后的读数误认为无读取自行增长。

首次文件映射Zone字节起点{first['当前映射Zone']}，最终映射起点{last['当前映射Zone']}。块轨迹.csv同时保留每个已访问Zone的RC、Channel/LUN/Plane/Block、Reset代际、设备状态及WP；计数账目.csv记录阈值事件与两种迁移读取口径。

原始日志rr.zenfs.log保留RR开始、实际复制、映射提交、源Reset和RR完成，独立核验已确认先提交映射再Reset。实验完成后归档正常关机。

这是受控ZenFS文件I/O，.sst仅为可迁移文件类别的功能载荷，不是RocksDB真实SST或性能实验。12秒空闲未重复是该观察窗口内的实测证据，不泛化为所有负载永不重复。
'''
    (output/'实验3分析.md').write_text(text)
    run.record(阶段='实验3完成，两次RR路径已核验，客机已退出',RR次数=2,ZC次数=0)
    print('实验3完成：'+str(output/'实验3分析.md'),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args();args.label=LABEL
    budget=preflight();output=IMAGE_DIR/(LABEL+'-results');require(not output.exists(),'不覆盖已有RR路径实验')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('RR路径功能实验认证（仅内存）：')
    output.mkdir();(output/'源码').mkdir()
    for name in ('run_rr_path_host.py','rr_path_device.cc','run_gc_path_host.py','gc_path_trace.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    (output/'源码/run_rr_path_guest.sh').write_text(GUEST)
    stamp(output/'预注册计划.json',dict(目的='实验3：独立验证阈值、搬迁、映射、Reset代际、目标重新计数及无读取时是否重复维护',
        GC启用=False,RR阈值=1024,后台RR=True,严格同步RR=True,内容字节=3<<20,
        自然首页API读取=2047,每次维护后触发请求重读计入RC=True,空闲观察秒=12,父镜像=str(PARENT),资源=budget,
        代码变更='只新增功能测试和隔离入口；不修改设备、库、RR逻辑或victim模型',
        收尾='归档哈希核验后正常关机；不保存内存NAND快照'))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
