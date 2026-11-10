"""W历史准入真实入口回归与提交生命周期测试；不启动客机。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from placement_survivor_source import BASE, MODE, prepare
from test_placement_ablation import environment, functions

ROOT = Path(__file__).resolve().parents[1]


def extract(text, declaration):
    start = text.index(declaration)
    begin = text.index('{', start)
    depth, end = 1, begin + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


class SurvivorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rrzc-survivor-')
        cls.path = Path(cls.temp.name)
        prepare(cls.path / '独立副本')
        cls.fs = cls.path / '独立副本/源码/协同池副本/plugin/zenfs/fs'

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def build(self, code, name):
        source = self.path / (name + '.cc')
        source.write_text(code)
        exe = self.path / name
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
            '-I' + str(self.fs), '-I' + str(self.path), str(source), '-o', str(exe)], check=True)
        return exe

    def test_modes_history_and_lifecycle(self):
        code = r'''#include "rrzc_write_survivor.h"
#include "rrzc_colocation_scope.h"
#include <cassert>
#include <iostream>
int main(){using namespace rrzc_write_survivor;using rrzc_placement::Mode;
try {
const auto mode=rrzc_placement::ProcessSettings().mode;
std::cout<<rrzc_placement::Name(mode)<<":"<<rrzc_placement::ProcessSettings().PoolK();
for(auto m:{Mode::kLegacy,Mode::kG0,Mode::kG1,Mode::kG2,Mode::kG2WriteSurvivor})
 for(bool append:{false,true})for(uint8_t tag=0;tag<4;++tag)
  for(auto s:{State::kNew,State::kRecoveredUnknown,State::kCommitted})
   assert(Skip(m,append,tag,s)==(m==Mode::kG2WriteSurvivor && !append && tag==2 && s==State::kCommitted));
History h;assert(h.Get()==State::kNew);
h.AfterMapping(true,false,true);assert(h.Get()==State::kNew);
h.AfterMapping(true,true,false);assert(h.Get()==State::kNew);
h.AfterMapping(false,true,true);assert(h.Get()==State::kNew);
// 多extent复制期间不升级；只有共同的成功映射提交才升级。
for(int i=0;i<3;++i)assert(!Skip(Mode::kG2WriteSurvivor,false,2,h.Get()));
h.AfterMapping(true,true,true);assert(h.Get()==State::kCommitted);
assert(Skip(Mode::kG2WriteSurvivor,false,2,h.Get()));
assert(!Skip(Mode::kG2WriteSurvivor,false,3,h.Get()));
// H/HW后来降为W-only仍保留历史；后一次失败不抹去已有证据。
h.AfterMapping(true,false,true);assert(h.Get()==State::kCommitted);
History other;assert(other.Get()==State::kNew);
h.Recovered();assert(h.Get()==State::kRecoveredUnknown);
h.AfterMapping(true,false,true);assert(h.Get()==State::kRecoveredUnknown);
h.AfterMapping(true,true,true);assert(h.Get()==State::kCommitted);
// Reset与目标Zone无关：文件对象上的证据不随目标Zone复用串到新文件。
assert(other.Get()==State::kNew);
{rrzc_colocation::Scope outer(2,{7},21,h.Get());
 assert(rrzc_colocation::current.write_history==State::kCommitted);
 {rrzc_colocation::Scope inner(1,{8},22);assert(rrzc_colocation::current.write_history==State::kNew);}
 assert(rrzc_colocation::current.owner==21 && rrzc_colocation::current.write_history==State::kCommitted);
}
assert(rrzc_colocation::current.owner==0);
setenv("ZENFS_RRZC_PLACEMENT_MODE","G0",1);
assert(rrzc_placement::ProcessSettings().mode==mode);return 0;
}catch(const std::invalid_argument&){return 3;}}'''
        exe = self.build(code, '历史与模式')
        for mode, k in [('G0', '0'), ('G1', '1'), ('G2', '1'), (MODE, '1')]:
            result = subprocess.run([str(exe)], env=environment(mode), capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, mode + ':' + k)
        for bad in ['', 'G2_WMIG_OFF', 'G2_HFALLBACK', MODE + ' ']:
            self.assertEqual(subprocess.run([str(exe)], env=environment(bad)).returncode, 3)
        for key in ['POOL_K', 'POOL_TAGS', 'STREAM_CLASSIFY', 'POOL_FLUSH_W', 'POOL_PAIR_ADMISSION']:
            env = environment(MODE);env['ZENFS_RRZC_' + key] = '1'
            self.assertEqual(subprocess.run([str(exe)], env=env).returncode, 3)

    def test_real_allocator_regression_and_no_token_on_gate(self):
        old = BASE / '源码/协同池副本/plugin/zenfs/fs'
        (self.path / '旧真实分配函数.inc').write_text(functions((old / 'rrzc_colocation_zenfs.inc').read_text(), 'OldDevice'))
        (self.path / '新真实分配函数.inc').write_text(functions((self.fs / 'rrzc_colocation_zenfs.inc').read_text(), 'NewDevice'))
        code = (ROOT / 'tests/placement_native_harness.cc').read_text()
        code = '#include "rrzc_write_survivor.h"\n' + code
        code = code.replace('  Env::WriteLifeTimeHint lifetime_=', '  std::atomic<uint64_t> used_capacity_{0};\n  Env::WriteLifeTimeHint lifetime_=')
        code = code.replace('sources;};', 'sources;uint64_t owner=0;rrzc_write_survivor::State write_history=rrzc_write_survivor::State::kNew;};')
        code = code.replace('struct IOStatus {', 'namespace rrzc_cp {enum {kMode,kAttempt,kG1Skip,kPoolTarget};template<class... A>void Emit(int,uint64_t,uint64_t,uint64_t,std::initializer_list<uint64_t> ={}){}}\nstruct IOStatus {')
        code = code.replace('inline bool Enabled() { return true; }', 'inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        code = code.replace('  int logger_=0;', '  uint64_t CompositionDevice() const {return 1;}\n  int logger_=0;')
        code = code.replace('  switch(scenario) {',
            '  current.write_history=static_cast<rrzc_write_survivor::State>(std::atoi(std::getenv("测试历史")));\n  switch(scenario) {')
        marker = '      assert(rrzc_colocation::current.tag=='
        code = code.replace(marker, '''      if(mode==rrzc_placement::Mode::kG2WriteSurvivor) {
        const bool gate=!new_file && rrzc_colocation::current.tag==rrzc_colocation::Tag::kShortLived &&
          rrzc_colocation::current.write_history==rrzc_write_survivor::State::kCommitted;
        if(gate)assert(!right && b.observed==0 && b.get_active==0 && b.put_active==0 && b.put_open==0 && b.zone.acquired==0);
        else assert(before==b.Result(right));
      }
''' + marker)
        exe = self.build(code, '实际入口回归')
        for mode in ['G0', 'G1', 'G2', MODE]:
            for state in range(3):
                env = environment(mode);env['测试历史'] = str(state)
                subprocess.run([str(exe), mode], env=env, check=True, capture_output=True)

    def test_disk_format_core_paths_and_commit_position(self):
        old = BASE / '源码/协同池副本/plugin/zenfs/fs'
        for name in ['zbd_zenfs.cc', 'zbd_zenfs.h', 'rrzc_colocation_pool.h', 'rrzc_stream_profile.h', 'rrzc_placement_trace.h']:
            self.assertEqual((old / name).read_bytes(), (self.fs / name).read_bytes(), name)
        for declaration in ['void ZoneFile::EncodeTo(', 'Status ZoneFile::MergeUpdate(']:
            self.assertEqual(extract((old / 'io_zenfs.cc').read_text(), declaration),
                             extract((self.fs / 'io_zenfs.cc').read_text(), declaration))
        before = (old / 'fs_zenfs.cc').read_text()
        after = (self.fs / 'fs_zenfs.cc').read_text()
        start = after.index('    // 只在真实映射提交成功后发布证据；')
        end = after.index('    if (!sync_status.ok()) s = sync_status;', start)
        self.assertGreater(start, after.index('IOStatus sync_status = SyncFileExtents('))
        after = after[:start] + after[end:]
        after = after.replace('rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation,\n'
            '        zfile->CompositionOwner(),zfile->WriteSurvivorState());',
            'rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation,zfile->CompositionOwner());')
        self.assertEqual(before, after)
        adapter = (self.fs / 'rrzc_colocation_zenfs.inc').read_text()
        self.assertLess(adapter.index('rrzc_write_survivor::Skip('), adapter.index('  auto observed=ColocationObservation(true);', adapter.index('IOStatus ZonedBlockDevice::TryColocation')))
        self.assertNotIn('SkipWriteMigration', adapter)
        self.assertNotIn('rrzc_hot_fallback', after)
        self.assertEqual((self.fs / 'rrzc_placement_mode.h').read_bytes(), (self.fs.parents[3] / 'rrzc_placement_mode.h').read_bytes())
        proof = json.loads((self.path / '独立副本/副本身份.json').read_text())
        self.assertFalse(proof['磁盘编码改变'])
        output = self.path / '独立副本'
        self.assertEqual((output / '代码差异.patch').read_bytes(), (output / 'W历史准入差异.patch').read_bytes())
        self.assertNotIn('G2_WMIG_OFF', (output / '代码差异.patch').read_text())


if __name__ == '__main__': unittest.main()
