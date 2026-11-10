"""退出顺序回归：先构造的全局拥有者晚于观测器清理，观测器不能释放后再写。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from analyze_placement_trace import rows

ROOT=Path(__file__).resolve().parents[1]
CODE=r'''#include "rrzc_placement_trace.h"
#include <cassert>
struct Late {~Late(){assert(!rrzc_placement_trace::Enabled());rrzc_placement_trace::Emit(rrzc_placement_trace::kClock,0,0,999);}};
Late& Owner(){static Late value;return value;}
int main(){Owner();for(unsigned i=0;i<100;++i)rrzc_placement_trace::Emit(rrzc_placement_trace::kClock,0,0,i);}
'''


class ExitTest(unittest.TestCase):
    def test_asan_late_global_cleanup_and_normal_seal(self):
        with tempfile.TemporaryDirectory() as name:
            work=Path(name)
            (work/'rrzc_placement_trace.h').write_bytes((ROOT/'model/rrzc_placement_trace_exit.h').read_bytes())
            (work/'probe.cc').write_text(CODE)
            binary=work/'probe'
            subprocess.run(['g++','-std=c++17','-O1','-g','-fsanitize=address','-pthread',str(work/'probe.cc'),'-o',str(binary)],check=True)
            # 当前执行器受ptrace管理，LSan不支持；ASan越界/释放后使用检查保持开启。
            env=dict(os.environ,ZENFS_RRZC_PLACEMENT_TRACE=str(work/'trace.bin'),ASAN_OPTIONS='detect_leaks=0')
            result=subprocess.run([str(binary)],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('ERROR: AddressSanitizer',result.stderr)
            self.assertEqual(len(list(rows(work/'trace.bin'))),100)

    def test_fixed_source_preserves_hot_algorithm(self):
        old=(ROOT/'model/rrzc_placement_trace.h').read_text()
        new=(ROOT/'model/rrzc_placement_trace_exit.h').read_text()
        self.assertEqual(old[old.index('class HeatProjection'):],new[new.index('class HeatProjection'):])
        self.assertIn('if(sealed_ || failed_)return;',new)
        self.assertIn('std::vector<Event>().swap(events_);',new)

    def test_late_device_owner_keeps_observation_until_final_cleanup(self):
        code=CODE.replace('#include <cassert>','#include <cassert>\n#include <memory>')
        code=code.replace('struct Late {~Late(){assert(!rrzc_placement_trace::Enabled());',
            'struct Late {std::unique_ptr<rrzc_placement_trace::DeviceLease> lease;~Late(){assert(rrzc_placement_trace::Enabled());')
        code=code.replace('Owner();for(', 'Owner().lease.reset(new rrzc_placement_trace::DeviceLease);for(')
        with tempfile.TemporaryDirectory() as name:
            work=Path(name);(work/'rrzc_placement_trace.h').write_bytes((ROOT/'model/rrzc_placement_trace_exit.h').read_bytes())
            (work/'probe.cc').write_text(code)
            subprocess.run(['g++','-std=c++17','-O1','-g','-fsanitize=address','-pthread',str(work/'probe.cc'),'-o',str(work/'probe')],check=True)
            env=dict(os.environ,ZENFS_RRZC_PLACEMENT_TRACE=str(work/'trace.bin'),ASAN_OPTIONS='detect_leaks=0')
            result=subprocess.run([str(work/'probe')],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            data=list(rows(work/'trace.bin'));self.assertEqual(len(data),101)
            self.assertEqual(data[-1][6],999,'最终设备清理事件被提前丢弃')


if __name__=='__main__':unittest.main()
