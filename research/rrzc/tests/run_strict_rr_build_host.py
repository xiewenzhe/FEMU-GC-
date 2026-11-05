#!/usr/bin/env python3
"""严格RR库隔离编译；记录新FEMU版本，不将旧实验配置误称相同设备语义。"""
import argparse
import getpass
import json
from pathlib import Path
import signal
import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest
from run_window_credit_host import WindowRun,resources,digest
from run_rr_fair_long_v2_host import CollectedRun
from run_sst_probe_host import SstRun

LABEL='joint-strict-rr-build-v1-20261022'
SOURCE=IMAGE_DIR/'joint-expected-ycsb-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-expected-ycsb-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc'
FILES=[ROOT/'tests/build_strict_rr_guest.sh',ROOT/'zenfs-strict-rr.patch',ROOT/'model/rrzc_strict_io.h']

class StrictBuild(WindowRun):
    close_guest=CollectedRun.close_guest
    finish=CollectedRun.finish
    def __init__(self,*args):
        super().__init__(*args);self.deployed=False

    def command(self,argv,timeout=60):
        if argv and str(argv[0]).endswith('qemu-system-x86_64'):
            # 只为独立严格RR实验加设备开关；原启动器及32GiB参数源不变。
            argv=[s+',zns_rr_strict=on' if s==base.OLD_DEVICE else s for s in argv]
            # 基类仍需识别原设备字符串，因此在下一层设备替换前保持原标记。
            if not any(s==base.OLD_DEVICE for s in argv):
                from finerr_profile import settings
                argv=[settings(self.profile)['设备参数']+',zns_rr_strict=on' if s==base.OLD_DEVICE+',zns_rr_strict=on' else s for s in argv]
                from run_tail_ablation_fixed_pair import FixedPair
                return FixedPair.command(self,argv,timeout)
        return super().command(argv,timeout)

    def collect(self,out,remote):
        if remote!=self.target:return super().collect(out,remote)
        SstRun.collect(self,out,remote)
        archive=f'{self.target}-{self.state["当前阶段"]}-evidence.tar.gz'
        require(digest(out/Path(archive).name)==self.ssh(f'sha256sum {archive}').split()[0],'构建归档哈希不符')

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        for p in [*FILES,Path(__file__).resolve()]:(frozen/p.name).write_bytes(p.read_bytes())
        out=self.output/'build';out.mkdir();expected=json.loads(MANIFEST.read_text())
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'父库实际身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(frozen/'parent.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_strict_rr_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译严格RR库，旧模型不变，未开始YCSB')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','严格RR构建失败，日志保留并停止')
        software=digest_manifest((built/'software.sha256').read_text())
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='严格RR库构建通过，已归档正常关闭')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    a.label=LABEL;a.phase='build';a.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    out,profile=base.preflight(a);before=resources(profile)
    for stage in ('build','smoke-greedy','smoke-expected','short-greedy','short-expected'):
        archive=SOURCE/stage/'阶段归档.json';s=json.loads(archive.read_text())
        require(s['完整'] and not s['客机仍存活'],'旧实验未收尾')
    if a.check:print(json.dumps(before,ensure_ascii=False));return
    password=getpass.getpass('严格RR隔离构建认证（仅内存）：');out.mkdir()
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (out/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (out/'预注册计划.json').write_text(json.dumps(dict(父镜像=str(PARENT),FEMU二进制SHA256=binary,
        设备额外开关='zns_rr_strict=on',新行为='自身到阈值后刷新读豁免，其他块搬迁照常计数；普通读到阈值同步RR',
        保护='单台约8GiB加16GiB余量，归档后正常关闭，不重跑或降低门槛',初始资源=before),ensure_ascii=False,indent=2))
    r=StrictBuild(a,password,out,profile);r.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:r.build()
    except BaseException as error:r.record(阶段='严格RR构建停止，未自动重跑',错误=str(error));raise
    finally:r.password=None

if __name__=='__main__':main()
