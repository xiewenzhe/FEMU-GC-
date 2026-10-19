#!/usr/bin/env python3
"""只增加512MiB初始真实数据；单次Greedy观察，不调整生产门槛。"""
import argparse,getpass,hashlib,json,os,shlex,socket,time
from pathlib import Path
from types import SimpleNamespace
from run_cost_rank_host import RankRun,PORT,running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_rocksdb_pressure import audit,LABEL,gc_audit

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'cost-rank-20261016-build.qcow2'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT=f'/home/femu/rrzc-stream-results/{LABEL}'
TESTS=PROJECT/'research/rrzc/tests'
SOURCES=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc','run_rocksdb_pressure_guest.sh']

def core_identity():
    return {n:d for n,d in json.loads((PROJECT/'research/rrzc/results/cost-rank-20261016/同库身份.json').read_text()).items()
            if not n.startswith('/')}

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'输出、父镜像或KVM不符')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8090已占用')
    for path in (IMAGE_DIR/f'{LABEL}-greedy.qcow2',Path(f'/tmp/{LABEL}-greedy.pid'),
                 Path(f'/tmp/{LABEL}-greedy.qmp'),Path(f'/tmp/{LABEL}-greedy-ssh.sock')):
        require(not path.exists(),'目标已存在：'+str(path))
    for path,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(path);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')
    previous=PROJECT/'research/rrzc/results/rocksdb-observe-20261016/运行归档/rocksdb-observe-20261016'
    r=gc_audit((previous/'zenfs.run.log').read_text().splitlines())
    require(r['进入选区数']==1 and r['进入选区详情'][0]['动态无效比例门槛']==100 and r['至少一个合格单区的轮数']==0,'旧无候选原因不符')

class PressureRun(RankRun):
    label_prefix=LABEL
    parent_image=PARENT
    def diagnose(self):
        state=json.loads((OUTPUT/'状态.json').read_text());self.state=state
        pid=state['当前客机PID'];image=str(IMAGE_DIR/f'{LABEL}-greedy.qcow2')
        require(state['当前镜像']==image and f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode()
                in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),'不是本轮失败客机')
        self.socket=f'/tmp/{LABEL}-greedy-ssh.sock'
        failed=OUTPUT/'运行归档'/LABEL
        require((failed/'run.exit').read_text().strip()=='1' and not (failed/'verify.exit').exists(),'不是原业务失败且未校验的现场')
        self.ssh(f'pid=$(cat {RESULT}/worker.pid); test ! -e /proc/$pid; test ! -e {DIRECTORY}/diagnostic.finished; test ! -e {RESULT}/zenfs.verify.log')
        backup=OUTPUT/'原始失败状态.json';require(not backup.exists(),'已做过诊断，拒绝重复');backup.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        command=(f'status=0; env RRZC_TEST_DB_PROFILE=pressure512 ZENFS_RR_ENABLE=0 '
            f'{DIRECTORY}/rocksdb_observe_test verify {RESULT} > {DIRECTORY}/diagnostic-verify.stdout.log 2>&1 || status=$?; '
            f'printf "%s\\n" "$status" > {DIRECTORY}/diagnostic.exit; touch {DIRECTORY}/diagnostic.finished')
        self.root('nohup setsid bash -c '+shlex.quote(command)+f' < /dev/null > {DIRECTORY}/diagnostic-wrapper.log 2>&1 &')
        self.record(阶段='原业务失败现场仅做只读最新值诊断，不补写原verify.exit，不改RR／GC')
        self.observe_until(f'{DIRECTORY}/diagnostic.finished',f'tail -1 {DIRECTORY}/diagnostic-verify.stdout.log',120)
        out=OUTPUT/'只读诊断归档';out.mkdir()
        errors=self.collect(out,[f'{DIRECTORY}/diagnostic-verify.stdout.log',f'{DIRECTORY}/diagnostic.exit',f'{RESULT}/zenfs.verify.log'])
        require(not errors,'只读诊断归档不全')
        self.record(阶段='只读诊断已归档，原业务仍失败，客机与原始证据保留',诊断校验退出码=(out/'diagnostic.exit').read_text().strip())
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['Greedy候选覆盖']
        plan=dict(初始数据唯一变化='2GiB增至2.5GiB，增加131072个真实键',初始键数=655360,值字节=4096,
            覆盖键范围=262144,读冷键号=524287,混合业务秒数=120,覆盖写键数=983040,读次数=48000,
            与旧场景比较='混合读写请求逐项相同；额外初始键保留，不手动删SST',
            策略='Greedy＋原V1影子',生产模型修改=False,GC启动比例=20,GC斜率=3,RR阈值=4096,
            回收资格MiB=192,额外预算MiB=16,紧迫界限秒数=10,
            目标='原门槛下实际ZC及至少两个合格单区的真实候选机会；不足也如实保留，不保证达成',
            解释='旧场景20%对应原无效门槛100%，不是192MiB预算先阻止了所有候选',
            失败自动重跑=False,超时秒数=900,其他安排='编译、计划边界和普通文件系统最小回归后才初始化新全空NVMe')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot=OUTPUT/'实验源';snapshot.mkdir()
        self.command(['cp',*[str(TESTS/n) for n in SOURCES],str(Path(__file__).resolve()),str(snapshot)])
        (snapshot/'分析').mkdir()
        self.command(['cp',str(PROJECT/'research/rrzc/analyze_rocksdb_observe.py'),str(PROJECT/'research/rrzc/analyze_rocksdb_pressure.py'),str(snapshot/'分析')])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.record(阶段='固定512MiB初始增量计划已登记',固定计划=plan)
        self.start('greedy');core=core_identity();query='cd /home/femu/rocksdb; sha256sum '+' '.join(core)
        before=self.ssh(query)
        require({n:d for d,n in (s.split() for s in before.splitlines())}==core,'生产源／库身份不同')
        (OUTPUT/'生产身份.before.sha256').write_text(before)
        self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}')
        self.command(self.scp()+[str(TESTS/n) for n in SOURCES]+[f'femu@127.0.0.1:{DIRECTORY}/'])
        try:
            self.record(阶段='新负载Werror编译，生产库不重编')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/rocksdb_observe_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/rocksdb_observe_test '
                f'> {DIRECTORY}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/build.exit; exit "$status"',120)
            self.record(阶段='计划边界与普通文件系统真实RocksDB最小回归')
            self.ssh(f'set -e; g++ -std=c++17 -Wall -Wextra -Werror {DIRECTORY}/test_rocksdb_observe_plan.cc '
                f'-o {DIRECTORY}/plan; {DIRECTORY}/plan > {DIRECTORY}/plan.log; mkdir {DIRECTORY}/selftest; '
                f'status=0; RRZC_TEST_DB_PROFILE=pressure512 {DIRECTORY}/rocksdb_observe_test selftest {DIRECTORY}/selftest '
                f'> {DIRECTORY}/selftest.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/selftest.exit; exit "$status"',120)
            names=['rocksdb_observe_test',*SOURCES]
            expected={f'{DIRECTORY}/{n}':hashlib.sha256((TESTS/n).read_bytes()).hexdigest() for n in SOURCES}
            identity=self.ssh('sha256sum '+' '.join(f'{DIRECTORY}/{n}' for n in names))
            actual={n:d for d,n in (s.split() for s in identity.splitlines())}
            require(all(actual.get(n)==d for n,d in expected.items()),'测试源码或包装身份不符')
            (OUTPUT/'测试身份.sha256').write_text(identity)
            self.root(f'nohup setsid bash {DIRECTORY}/run_rocksdb_pressure_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.record(阶段='真实2.5GiB准备／120秒混合业务／最新值校验，已脱离SSH')
            self.observe_until(f'{DIRECTORY}/finished',f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            output=OUTPUT/'运行归档';output.mkdir();errors=self.collect(output,[DIRECTORY,RESULT])
            (OUTPUT/'生产身份.after.sha256').write_text(self.ssh(query))
        require(not errors,'归档不完整，保留客机')
        result=audit(OUTPUT);(OUTPUT/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='数据、原候选覆盖和混合请求一致性核验完成，正常关闭新客机')
        try:self.shutdown()
        except RuntimeError as error:
            require(str(error)=='新客机未正常退出，不强制终止或启动下一组','非关机等待超时')
            self.record(阶段='正常关机30秒未退出，仅继续观察，不强制终止',关机等待原文=str(error))
            for _ in range(60):
                if not Path(f'/proc/{self.state["当前客机PID"]}').exists():break
                time.sleep(1)
            require(not Path(f'/proc/{self.state["当前客机PID"]}').exists(),'新客机仍未退出，保留现场')
        self.password=None
        self.record(阶段='单次真实候选覆盖观察及核验完成，新客机已退出',已完成=['编译与最小测试','真实请求与最新值校验','原GC候选覆盖核验'],当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--diagnose',action='store_true');args=parser.parse_args()
    if args.diagnose:
        require(not args.check,'诊断与前置检查不能混用')
        password=getpass.getpass('本轮失败现场只读诊断认证（仅驻留内存）：')
        pair=PressureRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),password,OUTPUT)
        pair.diagnose();return
    preflight()
    if args.check:print('只读前置检查及旧无候选原因复算通过');return
    password=getpass.getpass('新隔离客机认证（仅驻留内存）：');OUTPUT.mkdir()
    pair=PressureRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),password,OUTPUT)
    try:pair.run()
    except Exception as error:pair.record(阶段='停止，保留客机和原始记录，不自动重跑',错误=str(error));raise

if __name__=='__main__':main()
