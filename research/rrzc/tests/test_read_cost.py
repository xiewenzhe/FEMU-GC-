"""验证被动读成本分账；不修改锁、读命令及请求返回语义。"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import read_cost_source
from run_integrated_greedy_host import PRIOR

ROOT=Path(__file__).resolve().parents[1]


class ReadCostTest(unittest.TestCase):
    def test_real_cpp_wait_body_command_partition(self):
        code=r'''#include "rrzc_read_cost.h"
#include <thread>
int main(){using namespace rrzc_read_cost;
Begin(9,42);auto begin=Now();Read read(3,65536,false);read.BeforeLock();
std::this_thread::sleep_for(std::chrono::milliseconds(2));read.Acquired();
auto io=IoBegin();std::this_thread::sleep_for(std::chrono::milliseconds(3));IoEnd(io);
read.Done();End(begin,Now());{Work w(1,9);std::this_thread::sleep_for(std::chrono::milliseconds(2));}}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'probe.cc').write_text(code)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(p/'probe.cc'),'-o',str(p/'probe')],check=True)
            env=dict(os.environ);env['RRZC_READ_COST']=str(p/'trace')
            subprocess.run([str(p/'probe')],env=env,check=True)
            raw=(p/'trace').read_bytes();self.assertEqual(raw[:16],b'RRZCRD01'+bytes(8))
            rows=list(struct.iter_unpack('<10Q',raw[16:]));front=next(r for r in rows if r[0]==1)
            self.assertEqual(front[1:3],(9,42));self.assertEqual(front[9],1)
            self.assertGreaterEqual(front[6],2000000);self.assertGreaterEqual(front[8],3000000)
            self.assertLessEqual(front[8],front[7]);self.assertLessEqual(front[6]+front[7],front[5]-front[4])
            self.assertEqual(next(r for r in rows if r[0]==2)[8],1)
            self.assertEqual(next(r for r in rows if r[0]==4 and r[1]==1)[2],1)

    def test_real_source_keeps_lock_and_command(self):
        old=PRIOR/PRIOR.name.removesuffix('-results');fs=old/'协同池副本/plugin/zenfs/fs'
        io=read_cost_source.io((fs/'io_zenfs.cc').read_text())
        backend=read_cost_source.zbdlib((fs/'zbdlib_zenfs.cc').read_text())
        self.assertEqual(io.count('auto guard=zbd_->StrictLock();'),1)
        self.assertIn('read_cost.BeforeLock();',io);self.assertIn('read_cost.Acquired();',io)
        self.assertEqual(backend.count('const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);'),1)
        self.assertIn('cmd.cdw12=7;',backend);self.assertIn('while(done<size)',backend)
        client=read_cost_source.client((old/'校准-db_bench_tool.cc').read_text())
        self.assertIn('rrzc_read_cost::Begin(trace.Count(),id);',client)
        self.assertIn('rrzc_read_cost::End(before,after);',client)
        metrics=read_cost_source.metrics((fs/'metrics.h').read_text())
        self.assertIn('assert(end_time_micro_ >= begin_time_micro_);',metrics)
        self.assertIn('steady_clock::now()',metrics)


if __name__=='__main__':unittest.main()
