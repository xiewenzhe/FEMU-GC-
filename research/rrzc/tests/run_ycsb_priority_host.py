#!/usr/bin/env python3
"""等待当前A空间组完成后安全接班，优先F/B/E；不改变负载、模型或旧结果。"""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import time

from run_ycsb_af_host import SuiteRun, PROJECT, IMAGE_DIR, ROOT, host_resources
from ycsb_af import LABELS, WORKLOADS, RECORDS, OPERATIONS, digest, require
from analyze_ycsb_af import compare_pair

SOURCE = IMAGE_DIR/'joint-ycsb32-af-v3-20261023-results'
LABEL = 'joint-ycsb32-priority-v1-20261023'
ORDER = ('f', 'b', 'e')
TRIGGER = 'full-a-space'


def process_identity(pid):
    """记录启动时钟以拒绝PID复用；不存在或僵尸进程不能当作在运行。"""
    p = Path('/proc')/str(pid)
    try:
        raw = (p/'stat').read_text().rsplit(')', 1)[1].split()
        if raw[0] == 'Z':
            return None
        return dict(PID=pid, UID=p.stat().st_uid, 启动时钟=raw[19],
                    参数=[s.decode() for s in (p/'cmdline').read_bytes().split(b'\0') if s])
    except FileNotFoundError:
        return None


def check_supervisor(identity):
    require(identity is not None and identity['UID'] == os.getuid(), '旧监督不存在或账号不符')
    require(len(identity['参数']) >= 2 and Path(identity['参数'][1]).name == 'run_ycsb_af_host.py',
            '旧监督不是本项目登记入口')
    return identity


def ready(source):
    """只有当前组完整校验和正常关机后才接班；不以装载完成或RR多少为条件。"""
    folder = Path(source)/TRIGGER
    result, archived = folder/'独立核验.json', folder/'阶段归档.json'
    if not result.exists() or not archived.exists():
        return False
    r, a = json.loads(result.read_text()), json.loads(archived.read_text())
    require(r['验收通过'] and r['标签'] == TRIGGER and a['完整'] and not a['客机仍存活'],
            '当前A空间组数据、归档或关机未通过，不进入优先实验')
    return True


class PriorityRun(SuiteRun):
    def __init__(self, args, password, output, profile):
        super().__init__(args, password, output, profile)
        # 新客机继承已验收build的只读程序，而不是凭新标签猜测远端程序目录。
        self.target = '/home/femu/'+SOURCE.name.removesuffix('-results')
        self.state.update(阶段='等待当前A空间组完成；不启动第二台客机',
                          当前阶段='等待接班', 原实验目录=str(SOURCE),
                          模式顺序=[f'full-{w}-{m}' for w in ORDER for m in mode_order(w)],
                          暂后置=['A模型组', 'C', 'D'],
                          接班原因='用户要求优先可能出现读风险与空间需求重叠的负载；不是按收益筛选试次')


def mode_order(letter):
    modes = list(LABELS)
    return modes if list(WORKLOADS).index(letter) % 2 == 0 else list(reversed(modes))


def handoff(run, old):
    deadline = time.monotonic()+8*3600
    while time.monotonic() < deadline:
        state = json.loads((SOURCE/'状态.json').read_text())
        if ready(SOURCE):
            run.record(阶段='当前A空间组已验收归档，准备让旧监督正常退出')
            break
        require(process_identity(old['PID']) == old, '当前组未完成前旧监督已退出或身份变化；不自动重跑')
        run.record(阶段='等待当前A空间组完成；不启动第二台客机',
                   原实验最近阶段=state['阶段'], 原实验最近观测=state.get('最近观测'))
        time.sleep(5)
    else:
        raise RuntimeError('等待当前组超过登记8小时，停止接班，不擅自终止原实验')

    # 旧入口没有实时队列，因此在其下一组进入受finally保护的arm后发送SIGTERM。
    # 只停止登记的监督，让原finally采证、归档和正常关机；绝不向QEMU强杀。
    deadline = time.monotonic()+240
    while time.monotonic() < deadline:
        current = process_identity(old['PID'])
        if current is None:
            break
        require(current == old, '旧监督PID被复用或参数变化，不发送信号')
        state = json.loads((SOURCE/'状态.json').read_text())
        next_tag = state.get('当前阶段')
        if next_tag != TRIGGER and state.get('当前客机PID'):
            run.record(阶段='安全切换旧监督，由其finally归档并正常关闭刚启动的后续组',
                       旧监督身份=old, 旧队列中止阶段=next_tag,
                       说明='后续组因用户调整调度中止，原始数据保留；不当成完成的性能试次')
            require(process_identity(old['PID']) == old, '发送信号前身份变化')
            os.kill(old['PID'], signal.SIGTERM)
            break
        time.sleep(1)
    else:
        raise RuntimeError('未观察到安全接班边界，停止；不在未确认状态终止进程')

    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        current = process_identity(old['PID'])
        if current is None:
            break
        require(current == old, '旧监督PID复用，不继续等待或发送信号')
        time.sleep(3)
    else:
        raise RuntimeError('旧监督收尾未在600秒结束；不启动新客机或强制关闭')
    state = json.loads((SOURCE/'状态.json').read_text())
    require(state.get('当前客机PID') is None, '旧客机仍在，不启动优先组')
    run.record(阶段='旧监督及客机已退出，检查资源后启动优先实验',
               原实验最终状态=state, 当前资源=host_resources(run.profile))


def freeze(output):
    shutil.copytree(SOURCE/'源码', output/'源码')
    (output/'源码'/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    for name in ('同库身份.json', '平台配置.json'):
        (output/name).write_bytes((SOURCE/name).read_bytes())
    proof = {n: digest(SOURCE/n) for n in ('同库身份.json', '平台配置.json', '预注册计划.json')}
    for w in WORKLOADS:
        path = SOURCE/f'smoke-{w}-配对核验.json'
        require(json.loads(path.read_text())['配对验收通过'], '源小测未通过：'+w)
        proof[str(path)] = digest(path)
    (output/'继承身份.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))


def preflight(args):
    require(args.allow_unlocked, '本轮仍须显式批准未锁页')
    require(re.fullmatch(r'joint-[a-z0-9-]+', args.label), '标签非法')
    source_plan = json.loads((SOURCE/'预注册计划.json').read_text())
    for name in ('run_ycsb_af_host.py', 'analyze_ycsb_af.py', 'ycsb_af.py', 'plot_ycsb_af.py'):
        current = Path(__file__).with_name(name) if name == 'run_ycsb_af_host.py' else ROOT/name
        require(digest(current) == digest(SOURCE/'源码'/name),
                '与已验收入口或分析器不同，不能声称只调整调度：'+name)
    require(digest(PROJECT/'build-femu/qemu-system-x86_64') == source_plan['FEMU二进制SHA256'],
            'FEMU二进制漂移')
    profile = json.loads((SOURCE/'平台配置.json').read_text())
    require(profile['命名空间']['容量字节'] == 32 << 30, '不允许改变32GiB配置')
    state = json.loads((SOURCE/'状态.json').read_text())
    require('build' in state['已完成'], '缺少已验收构建')
    for tag in ('build', *[f'smoke-{w}-{m}' for w in WORKLOADS for m in LABELS]):
        a = json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(a['完整'] and not a['客机仍存活'], '源阶段未归档退出：'+tag)
    old = check_supervisor(process_identity(state['监督进程PID']))
    output = IMAGE_DIR/(args.label+'-results')
    require(not output.exists(), '拒绝覆盖新优先实验目录')
    build = IMAGE_DIR/(SOURCE.name.removesuffix('-results')+'-build.qcow2')
    require(build.is_file(), '缺少已验收构建镜像')
    for tag in ('build', *[f'full-{w}-{m}' for w in ORDER for m in LABELS]):
        name = args.label+'-'+tag
        for p in (IMAGE_DIR/(name+'.qcow2'), Path('/tmp')/(name+'.pid'),
                  Path('/tmp')/(name+'.qmp'), Path('/tmp')/(name+'-ssh.sock')):
            require(not p.exists(), '新目标已存在：'+str(p))
    return output, profile, old, build, source_plan['FEMU二进制SHA256']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label', default=LABEL)
    p.add_argument('--allow-unlocked', action='store_true')
    p.add_argument('--check', action='store_true')
    p.add_argument('--detach', action='store_true')
    args = p.parse_args()
    output, profile, old, build, binary = preflight(args)
    if args.check:
        print(json.dumps(dict(只读预检通过=True, 待接班监督=old,
            优先顺序=list(ORDER), 不提前中断当前A空间组=True), ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('优先F/B/E实验认证（仅驻留内存）：')
    output.mkdir()
    freeze(output)
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='用户要求按联合维护可能性优先验证，而非顺序跑所有负载',
        更新时间UTC=datetime.now(timezone.utc).isoformat(), 原实验=str(SOURCE),
        FEMU二进制SHA256=binary, 旧监督身份=old,
        等待完成组=TRIGGER, 优先负载=list(ORDER),
        理由={'F': '热点读与覆盖更新并存，可能同时累积风险和无效空间',
              'B': '读压力大、仍有少量更新，检验联合机会是否足够',
              'E': '源32GiB小测已触发RR，补充扫描场景；不保证有高空间收益'},
        暂后置=['A模型组', 'C', 'D'],
        不变='同一已验收构建和模型、32GiB、RR1024、GC20%、6291456键、120万业务、同种子、无WAL/块缓存、不限速',
        锁页='沿用用户允许的未锁页，仅QEMU子进程额度0，不改系统或旧保护',
        切换='当前A空间组完整验收后，精确核验旧监督UID/PID启动时钟/命令，SIGTERM让其finally归档并正常关机',
        保护='接班等待不启动客机；资源门槛、单台串行、数据/评分验收及正常收尾全部沿用',
        结果='每组配对均保留并自动出图；没有收益也保留，不以RR多少决定是否接班或继续',
        边界='只是优先验证，不能据此声称A至F全覆盖或稳定收益；不因标准负载未获益而悄悄改负载'), ensure_ascii=False, indent=2))
    if args.detach:
        r, w = os.pipe()
        pid = os.fork()
        if pid:
            os.close(w)
            require(os.read(r, 1) == b'1', '优先监督分离失败')
            os.close(r)
            print(f'优先监督PID={pid}，状态={output / "状态.json"}', flush=True)
            return
        os.close(r)
        os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(w, b'1')
        os.close(w)
    run = PriorityRun(args, password, output, profile)
    run.qemu_digest = binary
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        run.record(阶段='已登记F/B/E优先队列，等待当前A空间组归档后接班', 待接班监督=old)
        handoff(run, old)
        # 新建薄子镜像只复用停止父镜像中的程序；每个业务客机仍重建真实NAND。
        require(not (IMAGE_DIR/(args.label+'-build.qcow2')).exists(), '构建别名已存在')
        run.command(['./build-femu/qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
                     '-b', str(build), str(IMAGE_DIR/(args.label+'-build.qcow2'))])
        for letter in ORDER:
            for mode in mode_order(letter):
                run.arm('full', letter, mode)
            result = compare_pair(output, 'full', letter, RECORDS, OPERATIONS)
            (output/f'full-{letter}-配对核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            run.record(阶段=letter.upper()+'优先配对验收通过，全部客机已关闭', 最近差值=result['差值'])
            from plot_ycsb_af import render
            render(output)
        run.record(阶段='F/B/E三类优先配对完成，图表与CSV已生成，全部客机已关闭',
                   图表目录=str(output/'图表'))
    except BaseException as error:
        run.record(阶段='优先实验停止，原始结果保留，不自动重跑', 错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
