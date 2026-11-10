"""只做本地编译、真实函数桩回归及副本核验；不启动VM、不读写实验设备。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import placement_ablation_source as change
from build_placement_ablation import prepare,SOURCE,ROOT,digest,isolate_dependencies
from placement_ablation_config import resolve,plan


def environment(mode=None):
    env={k:v for k,v in os.environ.items() if not k.startswith('ZENFS_RRZC_')}
    if mode is not None:env['ZENFS_RRZC_PLACEMENT_MODE']=mode
    return env


def functions(adapter,device):
    init=adapter[adapter.index('void ZonedBlockDevice::InitColocationPool()'):]
    init=init[:init.index('\nuint8_t ZonedBlockDevice::ColocationTag')]
    target=adapter[adapter.index('IOStatus ZonedBlockDevice::TryColocation('):]
    return (init+'\n'+target).replace('ZonedBlockDevice::',device+'::')


class PlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-placement-stage1-')
        cls.path=Path(cls.tmp.name);cls.copy=cls.path/'隔离副本'
        cls.proof=prepare(SOURCE,cls.copy)
        cls.flags=['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
            '-I'+str(cls.copy/'新版源码'),'-I'+str(cls.path),'-I'+str(ROOT/'model')]

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def build(self,source,name):
        path=self.path/(name+'.cc');path.write_text(source);binary=self.path/name
        subprocess.run(self.flags+[str(path),'-o',str(binary)],check=True)
        return binary

    def test_mode_configuration_conflicts_and_process_is_immutable(self):
        code=r'''#include "rrzc_placement_mode.h"
#include <cassert>
#include <iostream>
int main(){try{const auto& c=rrzc_placement::ProcessSettings();
std::cout<<rrzc_placement::Name(c.mode)<<":"<<(c.PoolK()?c.PoolK():"未设置");
const auto original=c.mode;setenv("ZENFS_RRZC_PLACEMENT_MODE","G1",1);
assert(rrzc_placement::ProcessSettings().mode==original);return 0;
}catch(const std::invalid_argument&){return 3;}}'''
        exe=self.build(code,'模式解析')
        for mode,k in [('G0','0'),('G1','1'),('G2','1')]:
            r=subprocess.run([str(exe)],env=environment(mode),capture_output=True,text=True,check=True)
            self.assertEqual(r.stdout,f'{mode}:{k}')
        r=subprocess.run([str(exe)],env=environment(),capture_output=True,text=True,check=True)
        self.assertEqual(r.stdout,'旧入口:未设置')
        for invalid in ['', 'g1','G3','G2 ']:
            self.assertEqual(subprocess.run([str(exe)],env=environment(invalid)).returncode,3)
        for key in ['POOL_K','POOL_TAGS','STREAM_CLASSIFY','POOL_FLUSH_W',
                    'POOL_PAIR_ADMISSION','POOL_PAIR_OBSERVE','POOL_PAIR_COPY_BUDGET']:
            env=environment('G2');env['ZENFS_RRZC_'+key]='0'
            self.assertEqual(subprocess.run([str(exe)],env=env).returncode,3)

    def test_route_truth_table_including_hw_and_new_flush(self):
        code=r'''#include "rrzc_placement_mode.h"
#include <cassert>
int main(){using namespace rrzc_placement;
for(auto m:{Mode::kLegacy,Mode::kG0,Mode::kG1,Mode::kG2})
 for(bool fresh:{false,true})for(unsigned tag=0;tag<4;++tag)
  assert(SkipHotMigration(m,fresh,tag)==(m==Mode::kG1 && !fresh && (tag&1)));
}'''
        subprocess.run([str(self.build(code,'标签路由'))],check=True)

    def test_real_old_and_new_target_functions_with_stub_backends(self):
        old=(self.copy/'旧G2源码/rrzc_colocation_zenfs.inc').read_text()
        new=(self.copy/'新版源码/rrzc_colocation_zenfs.inc').read_text()
        (self.path/'旧真实分配函数.inc').write_text(functions(old,'OldDevice'))
        (self.path/'新真实分配函数.inc').write_text(functions(new,'NewDevice'))
        source=(ROOT/'tests/placement_native_harness.cc').read_text()
        exe=self.build(source,'真实目标函数桩回归')
        for mode in ['G0','G1','G2']:
            subprocess.run([str(exe),mode],env=environment(mode),check=True)

    def test_existing_hotness_algorithm_and_window_are_identical(self):
        old=(self.copy/'旧G2源码/rrzc_stream_profile.h').read_text()
        (self.path/'旧热度.h').write_text(old.replace('namespace rrzc_stream {','namespace old_stream {'))
        code=r'''#include "旧热度.h"
#include "rrzc_stream_profile.h"
#include <cassert>
int main(){old_stream::Profile a;rrzc_stream::Profile b;
for(unsigned i=0;i<150000;++i){
 if(i%5==0){old_stream::Tick();rrzc_stream::Tick();}
 else{auto x=a.Read((i%19)*4096,4096,65536);auto y=b.Read((i%19)*4096,4096,65536);
 assert(x.reads==y.reads && x.reused==y.reused && x.bytes==y.bytes && x.hot==y.hot && x.changed==y.changed);}
 if(i%31==0){auto x=a.Snapshot(65536);auto y=b.Snapshot(65536);assert(x.hot==y.hot && x.reads==y.reads);}}
assert(rrzc_stream::Enabled());}'''
        # 新头优先于model目录，以验证独立副本中的显式模式接线。
        exe=self.build(code,'热度回归')
        for mode in ['G0','G1','G2']:subprocess.run([str(exe)],env=environment(mode),check=True)

    def test_overlay_is_reversible_and_unrelated_sources_unchanged(self):
        old=self.copy/'旧G2源码';new=self.copy/'新版源码'
        self.assertEqual(change.undo_adapter((new/'rrzc_colocation_zenfs.inc').read_text()),
                         (old/'rrzc_colocation_zenfs.inc').read_text())
        for p in old.iterdir():
            if p.name not in change.TRANSFORMS:self.assertEqual(digest(p),digest(new/p.name),p.name)
        self.assertFalse(self.proof['完整ZenFS编译'])
        with self.assertRaises(ValueError):change.adapter((new/'rrzc_colocation_zenfs.inc').read_text())

    def test_flush_origin_and_legacy_classification_switches(self):
        # 两个头中的开关保持旧入口语义，显式三模式共同启用分类，不在G1关闭H。
        code=r'''#include "rrzc_flush_origin.h"
#include "rrzc_stream_profile.h"
#include <cassert>
#include <cstdlib>
int main(int argc,char** argv){assert(argc==2);bool expected=argv[1][0]=='1';
assert(rrzc_stream::Enabled()==expected);assert(rrzc_flush_origin::Enabled()==expected);
{rrzc_flush_origin::Scope s(true,123);assert(rrzc_flush_origin::current.active && rrzc_flush_origin::current.job==123);}
assert(!rrzc_flush_origin::current.active);}
'''
        exe=self.build(code,'来源开关')
        for mode in ['G0','G1','G2']:
            subprocess.run([str(exe),'1'],env=environment(mode),check=True)
        for bit in ['0','1']:
            env=environment();env['ZENFS_RRZC_STREAM_CLASSIFY']=bit;env['ZENFS_RRZC_POOL_FLUSH_W']=bit
            subprocess.run([str(exe),bit],env=env,check=True)

    def test_common_contract_cannot_reuse_vm_state_or_change_public_parameters(self):
        contract={'初始磁盘镜像':{'路径':'/仅供本地测试/父镜像','SHA256':'a'*64},
            '固定预填输入':{'路径':'/仅供本地测试/预填','SHA256':'b'*64},
            '请求事件流':{'路径':'/仅供本地测试/请求','SHA256':'c'*64},
            '公共参数':{'容量GiB':8,'ZoneMiB':128,'Open':8,'Active':12,'RR阈值':1024,
                'GC启动百分比':20,'选择器':'Greedy','随机种子':20261023,
                '缓存':False,'WAL':False,'串行运行':True},'模式':['G0','G1','G2']}
        result=plan(contract)
        for group in result['各组']:
            self.assertTrue(group['独立启动']);self.assertFalse(group['复用运行状态'])
            self.assertEqual(group['初始磁盘镜像'],contract['初始磁盘镜像'])
            self.assertEqual(group['公共参数'],contract['公共参数'])
        with self.assertRaises(ValueError):resolve('G1',{'ZENFS_RRZC_POOL_K':'1'})
        with self.assertRaises(ValueError):resolve('G1',{'ZENFS_RRZC_PLACEMENT_MODE':'G2'})
        contract['公共参数']['RR阈值']=128
        with self.assertRaises(ValueError):plan(contract)

    def test_dependency_outputs_stay_inside_overlay(self):
        old=['g++','-MMD','-MF','原树/zbd.d','-MT原树/zbd.o','-MQ','原树/q.o',
             '-Wp,-MMD,原树/other.d','-c','source.cc','-o','隔离对象.o']
        new=isolate_dependencies(old,self.path,'zbd')
        self.assertFalse(any('原树' in x for x in new))
        self.assertEqual(new[-4:],['-MF',str(self.path/'zbd.d'),'-MT',str(self.path/'zbd.o')])

    def test_g1_fallback_scanner_and_release_paths_remain_unchanged(self):
        old=self.copy/'旧G2源码';new=self.copy/'新版源码'
        self.assertEqual((old/'zbd_zenfs.cc').read_bytes(),(new/'zbd_zenfs.cc').read_bytes())
        text=(new/'zbd_zenfs.cc').read_text()
        self.assertIn('(!coloc_pool_ || !coloc_pool_->Contains(z->start_))',text)
        self.assertIn('coloc_migration_targets_.erase(zone)',text)
        # 普通迁移目标经同一个排除池成员的扫描；未新增第二条接受H的池入口。
        self.assertEqual(text.count('TryColocation('),2)
        fs=(new/'fs_zenfs.cc').read_text()
        self.assertEqual(fs,(old/'fs_zenfs.cc').read_text())
        self.assertEqual((new/'io_zenfs.cc').read_bytes(),(old/'io_zenfs.cc').read_bytes())


if __name__=='__main__':unittest.main()
