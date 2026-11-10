"""编译真实分类/分配函数，核验F不被当作寿命证据及旧三模式行为。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from placement_origin_source import BASE, ROOT, digest, io_header, prepare
from test_placement_ablation import environment, functions


def method(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


class OriginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rrzc-origin-')
        cls.path = Path(cls.temp.name)
        cls.proof = prepare(cls.path/'副本')
        cls.fs = cls.path/'副本/源码/协同池副本/plugin/zenfs/fs'
        cls.old = BASE/'源码/协同池副本/plugin/zenfs/fs'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def build(self, code, name):
        source = self.path/(name+'.cc')
        source.write_text(code)
        binary = self.path/name
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
            '-pthread', '-I'+str(self.fs), '-I'+str(self.path), str(source), '-o', str(binary)],
            check=True)
        return binary

    def test_source_never_grants_lifetime_evidence(self):
        code = r'''#include "rrzc_file_characteristics.h"
#include "rrzc_colocation_pool.h"
#include <cassert>
#include <cstring>
int main(){using namespace rrzc_file_characteristics;
static_assert(Observed(true,false).Source()==Origin::kFlush);
static_assert(!Observed(true,false).HasShortLifetimeEvidence());
static_assert(!Observed(true,true).HasShortLifetimeEvidence());
static_assert(static_cast<unsigned>(rrzc_colocation::Tag::kFlushOrigin)==2);
static_assert(rrzc_colocation::Tag::kFlushOrigin==rrzc_colocation::Tag::kShortLived);
for(bool source:{false,true})for(bool hot:{false,true}){
 const Observed facts(source,hot);
 assert(facts.FlushOrigin()==source && facts.ReadHot()==hot);
 assert(facts.LifetimeStatus()==Lifetime::kUnknown);
 assert(!facts.HasShortLifetimeEvidence());
 assert(facts.LegacySourceMask()==((source?2:0)|(hot?1:0)));
 assert(!std::strcmp(facts.Label(),hot?(source?"H+F":"H"):(source?"F":"U")));
}}
'''
        subprocess.run([str(self.build(code, '来源寿命独立'))], check=True)

    def test_real_zonefile_methods_preserve_read_count_and_legacy_fallback(self):
        old = method((self.old/'io_zenfs.h').read_text(), '  uint8_t ColocationTag()')
        new = (self.fs/'io_zenfs.h').read_text()
        observed = method(new, '  rrzc_file_characteristics::Observed PlacementCharacteristics()')
        current = method(new, '  uint8_t ColocationTag()')
        prefix = r'''#include "rrzc_file_characteristics.h"
#include <cassert>
#include <cstdint>
#include <string>
namespace rrzc_stream {bool enabled=true;bool Enabled(){return enabled;}}
struct Stats {bool hot;};
struct Profile {bool hot=false;int calls=0;Stats Snapshot(uint64_t){++calls;return {hot};}};
struct Device {uint8_t ColocationTag(const std::string&){return 2;}};
'''
        fields = r'''bool flush_origin_w_=false;Profile stream_profile_;uint64_t file_size_=123;
uint8_t coloc_tag_=0;Device device;Device* zbd_=&device;
std::string GetFilename(){return "/real-method.sst";}
'''
        code = prefix+'struct OldFile {\n'+fields+old+'\n};\nstruct NewFile {\n'+fields+observed+'\n'+current+'\n};\n'
        code += r'''int main(){static_assert(sizeof(OldFile)==sizeof(NewFile));
for(bool enabled:{false,true})for(bool source:{false,true})for(bool hot:{false,true})
 for(unsigned fallback:{0,1,2,3}){
  rrzc_stream::enabled=enabled;OldFile a;NewFile b;
  a.flush_origin_w_=b.flush_origin_w_=source;
  a.stream_profile_.hot=b.stream_profile_.hot=hot;a.coloc_tag_=b.coloc_tag_=fallback;
  assert(a.ColocationTag()==b.ColocationTag());
  assert(a.stream_profile_.calls==b.stream_profile_.calls);
  assert(b.stream_profile_.calls==(enabled?1:0));
  auto facts=b.PlacementCharacteristics();
  assert(facts.FlushOrigin()==source && facts.ReadHot()==(enabled&&hot));
  assert(!facts.HasShortLifetimeEvidence());
}}
'''
        subprocess.run([str(self.build(code, '真实ZoneFile分类回归'))], check=True)

    def test_real_allocator_three_modes_and_origin_only_reference(self):
        for root, name, klass in [(self.old, '旧真实分配函数.inc', 'OldDevice'),
                                  (self.fs, '新真实分配函数.inc', 'NewDevice')]:
            (self.path/name).write_text(functions((root/'rrzc_colocation_zenfs.inc').read_text(), klass))
        code = (ROOT/'tests/placement_native_harness.cc').read_text()
        code = code.replace('  Env::WriteLifeTimeHint lifetime_=',
            '  std::atomic<uint64_t> used_capacity_{0};\n  Env::WriteLifeTimeHint lifetime_=')
        code = code.replace('sources;};', 'sources;uint64_t owner=0;};')
        code = code.replace('struct IOStatus {',
            'namespace rrzc_cp {enum {kMode,kAttempt,kG1Skip,kPoolTarget};template<class... A>'
            'void Emit(int,uint64_t,uint64_t,uint64_t,std::initializer_list<uint64_t> ={}){}}\nstruct IOStatus {')
        code = code.replace('inline bool Enabled() { return true; }',
            'inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        code = code.replace('  int logger_=0;',
            '  uint64_t CompositionDevice() const {return 1;}\n  int logger_=0;')
        binary = self.build(code, '三模式真实分配回归')
        for mode in ('G0', 'G1', 'G2'):
            subprocess.run([str(binary), mode], env=environment(mode), check=True)

    def test_protected_bodies_and_transport_identity(self):
        self.assertTrue(all(self.proof['保护正文未改变'].values()))
        for name in ('rrzc_file_characteristics.h', 'rrzc_colocation_pool.h'):
            self.assertEqual(digest(self.fs/name), digest(self.fs.parents[3]/name))
        root = self.fs.parents[3]
        manifest = json.loads((root/'传输身份.json').read_text())
        self.assertTrue(all(digest(root/name)==sha for name, sha in manifest.items()))
        self.assertEqual(json.loads((root/'二阶副本身份.json').read_text())['新源码'],
                         {p.name:digest(p) for p in self.fs.iterdir() if p.is_file()})
        with self.assertRaises(ValueError):
            prepare(self.path/'副本')
        with self.assertRaises(ValueError):
            io_header((self.fs/'io_zenfs.h').read_text())
        self.assertFalse(self.proof['算法改变'])


if __name__ == '__main__':
    unittest.main()
