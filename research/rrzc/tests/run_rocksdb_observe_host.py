#!/usr/bin/env python3
"""隔离真实RocksDB观察；先读实际接口，再编译测试，失败保留客机。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_cost_rank_host import RankRun,PORT,running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require

LABEL='rocksdb-observe-20261016'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'cost-rank-20261016-build.qcow2'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT=f'/home/femu/rrzc-stream-results/{LABEL}'
TESTS=PROJECT/'research/rrzc/tests'

class ObserveRun(RankRun):
    label_prefix=LABEL
    parent_image=PARENT
    def identity(self):
        core={n:d for n,d in json.loads((PROJECT/'research/rrzc/results/cost-rank-20261016/同库身份.json').read_text()).items()
              if not n.startswith('/')}
        record=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(core))
        require({n:d for d,n in (s.split() for s in record.splitlines())}==core,'实际生产源／库不符')
        return record
    def prepare(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['Greedy观察']
        self.start('greedy')
        (OUTPUT/'生产身份.before.sha256').write_text(self.identity())
        archive=OUTPUT/'接口源码';archive.mkdir()
        names=['include/rocksdb/'+n+'.h' for n in ('db','options','listener','env','cache','table','metadata','write_batch','convenience')]
        names+=['plugin/zenfs/fs/'+n+'.h' for n in ('fs_zenfs','zbd_zenfs','snapshot')]
        self.command(self.scp()+[f'femu@127.0.0.1:/home/femu/rocksdb/{n}' for n in names]+[str(archive)])
        (OUTPUT/'接口身份.sha256').write_text(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(names)))
        self.record(阶段='接口已只读归档，模拟NVMe尚未初始化，待编译独立观察程序')
    def build(self):
        self.identity()
        self.ssh(f'test ! -e {DIRECTORY} && mkdir {DIRECTORY}')
        self.command(self.scp()+[str(TESTS/'rocksdb_observe_test.cc'),str(TESTS/'rocksdb_observe_plan.h'),f'femu@127.0.0.1:{DIRECTORY}/'])
        try:
            self.record(阶段='编译独立真实RocksDB观察程序，生产库不重编')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/rocksdb_observe_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/rocksdb_observe_test '
                f'> {DIRECTORY}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/build.exit; exit "$status"',120)
            self.record(阶段='普通文件系统真实RocksDB最小回归，不打开NVMe')
            self.ssh(f'mkdir {DIRECTORY}/selftest; status=0; {DIRECTORY}/rocksdb_observe_test selftest {DIRECTORY}/selftest '
                f'> {DIRECTORY}/selftest.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/selftest.exit; exit "$status"',120)
        finally:
            archive=OUTPUT/'编译与最小测试';archive.mkdir()
            self.collect(archive,[DIRECTORY])
        source=hashlib.sha256((TESTS/'rocksdb_observe_test.cc').read_bytes()).hexdigest()
        identity=self.ssh(f'sha256sum {DIRECTORY}/rocksdb_observe_test.cc {DIRECTORY}/rocksdb_observe_test {DIRECTORY}/rocksdb_observe_plan.h')
        require(identity.splitlines()[0].split()[0]==source,'测试源码与实际编译源不同')
        (OUTPUT/'测试身份.sha256').write_text(identity)
        self.record(阶段='编译和普通文件系统最小回归通过，待固定ZNS观察')
    def run_observation(self):
        require((OUTPUT/'生产身份.before.sha256').read_text()==self.identity(),'生产身份发生变化')
        archive=OUTPUT/'编译与最小测试'/Path(DIRECTORY).name
        require((archive/'build.exit').read_text().strip()==(archive/'selftest.exit').read_text().strip()=='0','尚未通过编译／最小测试')
        expected=(OUTPUT/'测试身份.sha256').read_text()
        require(expected==self.ssh(f'sha256sum {DIRECTORY}/rocksdb_observe_test.cc {DIRECTORY}/rocksdb_observe_test {DIRECTORY}/rocksdb_observe_plan.h'),'编译产物身份变化')
        plan=dict(性质='真实RocksDB事前因素观察，不比较新模型收益',策略='Greedy＋原模型影子',
            生产代码修改=False,RR阈值=4096,GC启动比例=20,初始键数=524288,值字节=4096,
            混合业务秒数=120,覆盖写键数=983040,覆盖键范围='前262144键，顺序循环',批量写键数=64,
            计划写键每秒=8192,计划读每秒=400,计划读次数=48000,
            读取计划='一半读被更新范围内16个固定键，一半读不更新范围的固定键；不按SST寿命调请求',
            数据产生='真实Flush和自动Compaction，无填充文件，无人工删除SST',
            采样秒数=1,分析过去窗口秒数=[2,10,30],后续成功删除观察秒数=10,
            候选因素=['同块近期读取持续性','同代际有效量变化','当前正在Compaction的有效文件占比','文件已存活年龄'],
            防混淆='Reset／最大块切换／快照覆盖不足显式未知；删除回调不是物理容量立即释放',
            判据='先核验自然事件和可观测覆盖；没有真实ZC候选或信号分布不足就报告缺口，不宣称模型有效',
            验证='普通文件系统最小回归后才初始化新空NVMe；退出后独立只读全键最新值校验',
            失败自动重跑=False,观察超时秒数=900)
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot=OUTPUT/'实验源';snapshot.mkdir()
        self.command(['cp',str(TESTS/'rocksdb_observe_test.cc'),str(TESTS/'rocksdb_observe_plan.h'),str(TESTS/'run_rocksdb_observe_guest.sh'),str(Path(__file__).resolve()),str(snapshot)])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.ssh(f'test ! -e {DIRECTORY}/run_rocksdb_observe_guest.sh && test ! -e {RESULT} && test ! -e {RESULT}-aux')
        self.command(self.scp()+[str(TESTS/'run_rocksdb_observe_guest.sh'),f'femu@127.0.0.1:{DIRECTORY}/'])
        try:
            self.root(f'nohup setsid bash {DIRECTORY}/run_rocksdb_observe_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.record(阶段='真实RocksDB准备／固定120秒混合业务／校验，已脱离SSH会话',固定计划=plan)
            self.observe_until(f'{DIRECTORY}/finished',f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            output=OUTPUT/'运行归档';output.mkdir()
            errors=self.collect(output,[DIRECTORY,RESULT])
            (OUTPUT/'生产身份.after.sha256').write_text(self.identity())
        require(not errors,'归档不完整，保留客机')
        for n in ('mkfs.exit','run.exit','verify.exit','runner.exit'):
            require((output/LABEL/n).read_text().strip()=='0',n+'失败，现场保留')
        self.record(阶段='业务和全键最新值校验通过，原始记录已归档，待独立核验')
    def resume_scheduler(self):
        failure=OUTPUT/'运行归档'/LABEL
        message=(failure/'mkfs.log').read_text()
        require('Current ZBD scheduler is not mq-deadline' in message and not (failure/'run.exit').exists(),
                '不符合仅初始化调度错误，不允许此恢复入口')
        # 只保留已定位的初始化失败；没有业务进程，不结束任何业务或丢弃结果。
        self.root(f'set -e; test ! -e {RESULT}/run.exit; test ! -e {RESULT}/worker.pid; '
            f'nvme zns report-zones /dev/nvme0n1 -o json > {DIRECTORY}/resume-zones.json; '
            'python3 -c '+"'import json; r=json.load(open(\""+DIRECTORY+'/resume-zones.json'+"\")); z=r[\"zone_list\"]; assert len(z)==32 and all(v[\"state\"]==\"EMPTY\" and v[\"wp\"]==v[\"slba\"] for v in z)'"+'; '+
            f'test ! -e {RESULT}.scheduler-failed; test ! -e {RESULT}-aux.scheduler-failed; '
            f'mv {RESULT} {RESULT}.scheduler-failed; mv {RESULT}-aux {RESULT}-aux.scheduler-failed; '
            f'for n in run_rocksdb_observe_guest.sh launcher.log launcher.exit finished; do '
            f'test ! -e {DIRECTORY}/$n.scheduler-failed; mv {DIRECTORY}/$n {DIRECTORY}/$n.scheduler-failed; done')
        for old,new in (('运行归档','初始化失败归档'),('实验源','初始化失败源码'),('预注册计划.json','初始化失败预注册计划.json')):
            require(not (OUTPUT/new).exists(),'失败证据保存目标已存在');(OUTPUT/old).rename(OUTPUT/new)
        self.record(阶段='已确认初始化前失败且设备全空，保留失败证据，仅修复调度器后手动继续一次')
        self.run_observation()
    def finalize(self):
        result=json.loads((OUTPUT/'独立核验.json').read_text())
        require(result['记录与数据核验通过'],'记录或数据尚未核验')
        require((OUTPUT/'生产身份.before.sha256').read_text()==self.identity(),'结束前生产身份改变')
        require(self.ssh(f'cat {DIRECTORY}/launcher.exit').strip()=='0','包装未成功结束')
        self.record(阶段='数据及记录核验通过，保留RR中止原文，正常关闭本轮观察客机')
        self.shutdown()
        old_error=self.state.pop('错误',None)
        self.record(阶段='真实RocksDB观察完成；存在RR中止，不作为正式性能基线',已完成=['接口核对','编译与普通文件系统最小回归','真实观察及全键最新值校验','独立采集核验'],
            当前客机PID=None,历史初始化错误=old_error,维护错误数=len(result['维护错误原文']))

def preflight_prepare():
    require(not OUTPUT.exists() and PARENT.is_file() and not running_parent(PARENT),'输出已存在或父镜像状态不符')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8090已占用')
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    stage=parser.add_mutually_exclusive_group(required=True)
    stage.add_argument('--prepare',action='store_true');stage.add_argument('--build',action='store_true');stage.add_argument('--run',action='store_true')
    stage.add_argument('--resume-scheduler',action='store_true')
    stage.add_argument('--finalize',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.prepare:
        preflight_prepare()
        if args.check:print('只读准备检查通过');return
        password=getpass.getpass('新隔离客机认证（仅驻留内存）：');OUTPUT.mkdir()
        pair=ObserveRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),password,OUTPUT)
    else:
        state=json.loads((OUTPUT/'状态.json').read_text());pid=state['当前客机PID']
        if args.finalize and pid is not None and not Path(f'/proc/{pid}').exists():
            require(state['当前镜像']==str(IMAGE_DIR/f'{LABEL}-greedy.qcow2') and
                    state.get('错误')=='新客机未正常退出，不强制终止或启动下一组','不是已发送正常关机后超时的本轮现场')
            result=json.loads((OUTPUT/'独立核验.json').read_text());require(result['记录与数据核验通过'],'数据尚未核验')
            backup=OUTPUT/'关机等待超时状态.json';require(not backup.exists(),'退出状态已确认，不重复写入')
            backup.write_text(json.dumps(state,ensure_ascii=False,indent=2))
            pair=ObserveRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),None,OUTPUT);pair.state=state
            previous=pair.state.pop('错误')
            pair.record(阶段='真实RocksDB观察完成；关机等待超时后确认客机已退出，未强制终止；存在RR中止',
                已完成=['接口核对','编译与普通文件系统最小回归','真实观察及全键最新值校验','独立采集核验'],
                当前客机PID=None,历史关机等待=previous,维护错误数=len(result['维护错误原文']))
            return
        require(f'file={state["当前镜像"]},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode()
                in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),'不是本轮隔离客机')
        require(state['当前镜像']==str(IMAGE_DIR/f'{LABEL}-greedy.qcow2'),'不是本轮隔离镜像')
        pair=ObserveRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),None,OUTPUT)
        pair.state=state;pair.socket=f'/tmp/{LABEL}-greedy-ssh.sock';pair.qmp=Path(f'/tmp/{LABEL}-greedy.qmp')
        if args.check:pair.identity();print('现有隔离客机身份检查通过');return
        if args.run or args.resume_scheduler:pair.password=getpass.getpass('本轮业务启动认证（仅驻留内存）：')
    try:
        if args.prepare:pair.prepare()
        elif args.build:pair.build()
        elif args.run:pair.run_observation()
        elif args.resume_scheduler:pair.resume_scheduler()
        else:pair.finalize()
    except Exception as error:
        pair.record(阶段='停止，保留客机和已有证据',错误=str(error));raise

if __name__=='__main__':main()
