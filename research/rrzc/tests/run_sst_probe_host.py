#!/usr/bin/env python3
"""真实YCSB生存机会短测：同库两种逻辑布局，一台串行，归档后正常关机。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal
import time

import run_joint_host as base
from run_joint_host import JointRun, require, PROJECT, IMAGE_DIR, digest_manifest

LABEL = 'joint-sst-ycsb-v1-20261020'
PARENT = IMAGE_DIR/'joint-recent-eta-v1-20261020-build.qcow2'
MANIFEST = IMAGE_DIR/'joint-recent-eta-v1-20261020-results/同库身份.json'
CLIENT_MANIFEST = IMAGE_DIR/'content-guard-20261020-results/同库身份.json'
TESTS = PROJECT/'research/rrzc/tests'
FILES = [TESTS/name for name in ('build_sst_probe_guest.sh', 'run_sst_probe_guest.sh',
    'workload-sst-probe.properties', 'sst_probe_trace.py', 'verify_ycsb.cc', 'sst_device_probe.cc')]


def interrupted(*_):
    raise KeyboardInterrupt


def business_modes(values):
    """允许先单组验证空间压力，默认两组语义不变；拒绝重复以免覆盖。"""
    require(values and len(values) == len(set(values)) and
            all(v in ('disjoint', 'overlap') for v in values), '业务模式为空、重复或未知')
    return ['smoke', *values]


class SstRun(JointRun):
    def collect(self, out, remote):
        if remote != self.target:
            return super().collect(out, remote)
        # 不归档可重建的旧对象与普通目录数据库；保留源码包、真实构建和自测证据。
        name = Path(self.target).name
        archive = f'{self.target}-{self.state["当前阶段"]}-evidence.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{name}/ycsb" '
            f'--exclude="{name}/selftest-*/db" -czf {archive} -C /home/femu {name}', 120)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}', str(out)], 180)
        self.command(['tar', '-xzf', str(out/Path(archive).name), '-C', str(out)])

    def build(self):
        frozen = self.output/'源码'; frozen.mkdir()
        for p in FILES+[Path(__file__).resolve(), Path(base.__file__).resolve()]:
            (frozen/p.name).write_bytes(p.read_bytes())
        # 分析入口也冻结，避免事后默默改变机会定义。
        (frozen/'分析').mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):
            (frozen/'分析'/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FILES}, ensure_ascii=False, indent=2))
        expected = json.loads(MANIFEST.read_text())
        old = json.loads(CLIENT_MANIFEST.read_text())
        prefix = '/home/femu/rrzc-ycsb-retention-20261017-tests/ycsb/'
        clients = {n: d for n, d in old.items() if n.startswith(prefix)}
        require(len(clients) >= 6, '缺少继承YCSB源码和二进制身份')
        out = self.output/'build'; out.mkdir()
        try:
            self.start('build')
            for values, name in ((expected, 'parent.software.sha256'), (clients, 'client.parent.sha256')):
                raw = self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(values))
                require(digest_manifest(raw) == values, '停止父镜像的软件身份不符：'+name)
                (frozen/name).write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            payload = [frozen/p.name for p in FILES]+[frozen/'parent.software.sha256', frozen/'client.parent.sha256']
            self.command(self.scp()+list(map(str, payload))+[f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_sst_probe_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='重链接当前库的独立YCSB与校验器，分离和重叠各双次普通目录自测')
            self.wait_for(self.target+'/build.finished', f'test ! -f {self.target}/ycsb-build.log || tail -1 {self.target}/ycsb-build.log', 900)
            # 只打包实际客户端源，不包含对象、二进制或测试数据库。
            self.ssh(f'tar --exclude="*.o" -czf {self.target}/client-source.tar.gz -C {self.target}/ycsb '
                     'Makefile core rocksdb rrzc_ycsb_audit.h', 60)
        finally:
            self.finish(out)
        built = out/Path(self.target).name
        require(all((built/n).read_text().strip() == '0' for n in ('build.exit', 'selftest.exit')),
                '真实构建或普通目录测试失败，不启动ZNS业务')
        software = digest_manifest((built/'software.sha256').read_text())
        require(all(software.get(n) == d for n, d in expected.items()), '不应修改父镜像核心库或模型')
        require(software[self.target+'/ycsb/ycsb'] != clients[prefix+'ycsb'], '未确认新YCSB二进制产生')
        require('librocksdb_debug.a' in (built/'ycsb-build.log').read_text(), '未记录真实静态重链接命令')
        require('ZENFS_RRZC_RECENT_ETA' in (built/'recent-eta-symbol.log').read_text(), '新二进制缺近期ETA开关')
        for mode in ('disjoint', 'overlap'):
            a, b = [built/f'selftest-{mode}-{repeat}' for repeat in ('first', 'second')]
            require((a/'audit.requests').read_bytes() == (b/'audit.requests').read_bytes(), '同配置请求不一致')
            require((a/'audit.expected').read_bytes() == (b/'audit.expected').read_bytes(), '同配置最新值不同')
            for folder in (a, b):
                require('键数=1024' in (folder/'verify.log').read_text(), '普通目录全键校验缺失')
        (self.output/'同库身份.json').write_text(json.dumps(software, ensure_ascii=False, indent=2))
        self.state['已完成'].append('build')
        self.record(阶段='YCSB真实重链接及四次普通目录测试通过，构建客机已退出')

    def observe(self, mode):
        remote = '/home/femu/rrzc-sst-results/'+mode
        started = changed = time.monotonic(); previous = None
        while time.monotonic()-started < 1500:
            raw = self.ssh(f'python3 - {remote} <<\'PY\'\n'
                'from pathlib import Path\nimport json,re,sys\n'
                'r=Path(sys.argv[1]); p=r/"ycsb.log"; text=p.read_text(errors="replace") if p.exists() else ""\n'
                'rows=re.findall(r"(\\d+) sec: (\\d+) operations;",text)\n'
                'log=r/"aux/rrzc-ycsb/LOG"\n'
                'errors=[s for s in log.read_text(errors="replace").splitlines() if re.search('
                'r"Background (compaction|flush) error|Zone allocation failure|Corruption:",s)] if log.exists() else []\n'
                'print(json.dumps(dict(完成=(r/"finished").exists(),装载完成="Load runtime(sec):" in text,'
                '最近状态=rows[-1] if rows else None,错误=errors[-3:]),ensure_ascii=False))\nPY')
            point = json.loads(raw)
            self.record(阶段=mode+'：真实ZNS短测及全键校验', 最近观测=point)
            with (self.output/'进度.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(模式=mode, **point), ensure_ascii=False)+'\n')
            require(not point['错误'], '出现真实后台错误；归档及关机，不自动重跑')
            if point['完成']:
                return
            key = (point['装载完成'], str(point['最近状态']))
            if key != previous:
                previous, changed = key, time.monotonic()
            require(time.monotonic()-changed < 180, '连续180秒无进展，先归档与正常关机')
            time.sleep(10)
        raise RuntimeError('本臂超过1500秒，先归档与正常关机')

    def arm(self, mode):
        self.parent_image = IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out = self.output/mode; out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+mode
        expected = json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(mode)
            raw = self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw) == expected, '运行前软件身份不一致')
            size_arg = '' if self.args.records == 327680 or mode == 'smoke' else ' '+str(self.args.records)
            self.root(f'nohup setsid bash {self.target}/run_sst_probe_guest.sh {mode}{size_arg} < /dev/null '
                      f'> {self.target}/{mode}.启动.log 2>&1 &')
            self.record(客机结果目录=remote)
            self.observe(mode)
        finally:
            self.finish(out, remote)
        result = out/mode
        require(all((result/n).read_text().strip() == '0'
            for n in ('wrapper.exit', 'business.exit', 'verify.exit', 'runner.exit')), '业务或全键校验失败')
        from analyze_sst_probe import analyze_arm
        outcome = analyze_arm(result, mode, expected_records=self.args.records)
        (out/'独立核验.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
        self.state['已完成'].append(mode)
        self.record(阶段=mode+'正确性及维护核验通过，已归档并正常关机', 最近结果={k:outcome[k]
            for k in ('ZC次数', 'RR次数', '总迁移字节', '原合格候选数', '近期风险候选数')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--records', type=int, choices=(327680,491520), default=327680)
    parser.add_argument('--modes', nargs='+', choices=('disjoint','overlap'), default=['disjoint','overlap'])
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args(); args.phase = 'build'; args.scenarios = []
    modes = business_modes(args.modes)
    base.PARENT, base.PARENT_MANIFEST = PARENT, MANIFEST
    output, profile = base.preflight(args)
    require(CLIENT_MANIFEST.is_file(), '缺继承客户端身份')
    for mode in modes:
        for path in (IMAGE_DIR/f'{args.label}-{mode}.qcow2', Path(f'/tmp/{args.label}-{mode}.pid'),
                     Path(f'/tmp/{args.label}-{mode}.qmp'), Path(f'/tmp/{args.label}-{mode}-ssh.sock')):
            require(not path.exists(), '新目标已存在，拒绝覆盖：'+str(path))
    if args.check:
        print('停止父镜像、4GiB锁页、串行预算及独立路径检查通过；未启动'); return
    password = getpass.getpass('本轮隔离客机认证（仅驻留内存）：')
    output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='近期块风险与真实SST退役的交集验证，不宣称优化或正式性能收益',
        父镜像=str(PARENT), 阶段=['同库重链接和普通目录自测', *modes], 装载键数=args.records,
        设备='4GiB/32个128MiB Zone/4KiB页/每区128块；FineRR每区组织缩小盘，非32GiB全规模',
        机制='两组同一近期ETA直接选区；GC20%、RR1024、原资格及安全复核不变，额外回收门槛0',
        负载=f'真实YCSB-C++派生：{args.records}个4KiB字段，400000次50/50请求，单线程种子20261017，1000次/秒，无WAL/无块缓存',
        登记业务模式=args.modes, 更新范围='disjoint为[4096,69632)，overlap为[0,65536)；读区[0,4096)，不承诺物理共置',
        压力检验='相对旧327680键仅增加装载量；区分进入GC、原空间资格、多安全候选、成功ZC；不保证一定有机会',
        正确性='普通目录每种配置两次且轨迹一致；小ZNS8192键/8192请求；每臂在线值及独立全键最新值校验',
        机会='真实同块近期ETA<=10秒、原资格及物理文件归属；将未来退役仅用于标签，不输入评分',
        指标='ZC/RR、总复制、成功Reset/按实测几何换算块擦除、均值及p99/p99.9/p99.99、吞吐量；不以限速吞吐代表饱和性能',
        收尾='逐臂归档、正常关机、PID退出及SSH控制收尾；无进展180秒/单臂1500秒则停止，不自动重跑'),
        ensure_ascii=False, indent=2))
    if args.detach:
        r, w = os.pipe(); pid = os.fork()
        if pid:
            os.close(w); require(os.read(r, 1) == b'1', '监督分离失败'); os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}', flush=True); return
        os.close(r); os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        os.dup2(fd, 1); os.dup2(fd, 2); os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(w, b'1'); os.close(w)
    signal.signal(signal.SIGTERM, interrupted)
    run = SstRun(args, password, output, profile)
    # JointRun内部管理参数只承载标签等；业务规模单独显式保存，不静默丢失。
    run.args.records = args.records
    run.state.update(装载键数=args.records, 业务模式=args.modes, 模式顺序=['build', *modes])
    run.state['边界'] = '真实YCSB功能短测，非32GiB或正式稳态收益对照；两臂仅改请求交集，不改模型'
    try:
        run.build()
        for mode in modes:
            run.arm(mode)
        run.record(阶段='小测试和所登记业务短测均完成、客机全部关闭，等待压力和候选覆盖解释')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑', 错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
