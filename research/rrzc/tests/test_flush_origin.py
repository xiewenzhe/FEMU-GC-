"""来源作用域跨翻译单元、线程隔离及独立源码变换核验。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import json
import shutil
import flush_origin_source as patch

ROOT=Path(__file__).resolve().parents[1]
PRIOR=Path('/home/data/xwz/rrzc-baselines/joint-controlled-sst-interleaved-v1-20261030-results')


class OriginTest(unittest.TestCase):
    def test_scope_cross_unit_thread_exception_and_default(self):
        a='''#include "rrzc_flush_origin.h"
bool other(){return rrzc_flush_origin::current.active && rrzc_flush_origin::current.job==42;}
'''
        b='''#include "rrzc_flush_origin.h"
#include <cassert>
#include <thread>
bool other();
int main(){using namespace rrzc_flush_origin;unsetenv("ZENFS_RRZC_POOL_FLUSH_W");assert(!Enabled());
assert(!other());{Scope scope(true,42);assert(other());std::thread t([]{assert(!current.active);});t.join();
{Scope disabled(false,9);assert(!other());}assert(other());
try{Scope nested(true,7);throw 1;}catch(int){}assert(other());}assert(!current.active);
setenv("ZENFS_RRZC_POOL_FLUSH_W","1",1);assert(Enabled());
setenv("ZENFS_RRZC_POOL_FLUSH_W","x",1);try{Enabled();assert(false);}catch(const std::invalid_argument&){}
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'a.cc').write_text(a);(p/'b.cc').write_text(b)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(p/'a.cc'),str(p/'b.cc'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)

    def test_frozen_patch_preserves_selection_and_uses_metadata_source(self):
        guest=PRIOR/PRIOR.name.removesuffix('-results');fs=guest/'协同池副本/plugin/zenfs/fs'
        for name, transform in (('io_zenfs.h',patch.header),('io_zenfs.cc',patch.io),('fs_zenfs.cc',patch.fs)):
            old=(fs/name).read_text();new=transform(old)
            with self.assertRaises(ValueError):transform(new)
            if name=='fs_zenfs.cc':
                body=lambda text:text.split('void ZenFS::GCWorker()',1)[1].split('Status ZenFS::',1)[0]
                self.assertEqual(body(old),body(new))
            if name=='io_zenfs.cc':
                self.assertIn('if(!membership.empty() || flush_origin_w_)',new)
                self.assertIn('coloc_tag_!=2',new)
                self.assertIn('|| ColocationTag())return;',new)
                self.assertIn('flush_origin_w_=update->flush_origin_w_',new)
        old=(PRIOR/'源码/诊断-flush_job.cc').read_text();new=patch.flush(old)
        self.assertEqual(new.split('      s = BuildTable(',1)[1],old.split('      s = BuildTable(',1)[1])
        self.assertNotIn('compaction_job',new)

    def test_business_switch_and_H_only_initial_source(self):
        source=(PRIOR/'源码/run_pool_business_guest.sh').read_text();new=patch.business_wrapper(source)
        subprocess.run(['bash','-n'],input=new,text=True,check=True)
        self.assertLess(new.index('flush_w=1;hotspot=1;phase run'),new.index('--benchmarks=rrzctick,rrzclayout'))
        self.assertIn('flush_w=0;hotspot=0;phase verify',new)
        for token in ('ZENFS_RRZC_CANDIDATE_POOL=original','--reads=393216 --writes=737280'):
            self.assertIn(token,new)
        from pool_flush_guest import validate_source
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'source').mkdir()
            info=json.loads((PRIOR/'源码/受控标签来源.json').read_text())
            info.update(标签范围='业务Flush',标签={n:t for n,t in info['标签'].items() if t=='H'})
            (p/'受控标签来源.json').write_text(json.dumps(info));(p/'受控标签.txt').write_text(''.join(n+' H\n' for n in sorted(info['标签'])))
            data=PRIOR/PRIOR.name.removesuffix('-results')/'source'
            shutil.copyfile(data/'装载-0.sst.csv',p/'source/装载-0.sst.csv')
            (p/'source/load.zenfs.log').write_text('协同池取得目标：阶段=新文件\n')
            (p/'source/load.log').write_text('无W标记\n');validate_source(p)
            proof=json.loads((p/'真实SST布局核验.json').read_text());self.assertEqual(proof['混合Zone'],[])
            (p/'source/load.log').write_text('Flush来源W标记：不应存在\n')
            with self.assertRaises(AssertionError):validate_source(p)

    def test_builder_isolated_import(self):
        names=('build_flush_origin_guest.py','build_sst_interleaved_guest.py','build_pool_dbbench_guest.py',
            'build_all_blocks_guest.py','build_all_blocks_dbbench_guest.py','flush_wait_source.py',
            'sst_interleaved_source.py','sst_lifetime_source.py','calibration_source.py','flush_origin_source.py')
        with tempfile.TemporaryDirectory() as tmp:
            for n in names:shutil.copyfile(ROOT/'tests'/n,Path(tmp)/n)
            subprocess.run(['python3','-I','-c','import sys;sys.path.insert(0,sys.argv[1]);import build_flush_origin_guest',tmp],check=True)

    def test_probe_checks_isolated_device_and_initializes_scheduler(self):
        path=ROOT/'tests/run_flush_probe_guest.sh';text=path.read_text()
        subprocess.run(['bash','-n',str(path)],check=True)
        self.assertIn('8589934592',text)
        self.assertLess(text.index("printf 'mq-deadline"),text.index('"$target/flush_metadata_probe"'))
        self.assertNotIn('reset-zone',text)

    def test_no_gc_is_not_missing_sampler(self):
        from run_sst_interleaved_host import sampling_guard
        log='全块采样范围：未满区=1，代表诊断=1'
        sampling_guard(log,dict(逐轮选择=[]))
        with self.assertRaises(ValueError):sampling_guard('',dict(逐轮选择=[]))
        with self.assertRaises(ValueError):sampling_guard(log,dict(逐轮选择=[dict(候选=[{}])]))
        sampling_guard(log+'\n全块代表门控：',dict(逐轮选择=[dict(候选=[{}])]))


if __name__=='__main__':unittest.main()
