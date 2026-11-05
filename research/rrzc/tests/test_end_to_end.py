"""真实线程队列及逐笔确认格式测试；伪API仅作验证，不是实验结果。"""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from analyze_end_to_end import analyze
import end_to_end_source
from run_integrated_greedy_host import PRIOR

ROOT=Path(__file__).resolve().parents[1]


class EndToEndTest(unittest.TestCase):
    def test_cpp_bounded_queue_and_completion_audit_rejects_early_ack(self):
        code=r'''#include "rrzc_end_to_end.h"
#include <string>
int main(int argc,char** argv){
 if(argc==2){rrzc_end_to_end::Queue queue(rrzc_sst_lifetime::Plan::Make(true,true),1);return 0;}
 using namespace rrzc_end_to_end;
 const auto plan=rrzc_sst_lifetime::Plan::Make(true,true);Queue queue(plan,64);Ledger done(64,32);
 rrzc_sst_lifetime::Trace trace(argv[1]);uint64_t seq=0;
 auto one=[&](bool write,uint64_t key){auto r=queue.Take(seq,write,key);auto a=Now(),b=Now();
 done.Api(r,a,b);trace.Record(write,key,a,b,Now());++seq;};
 auto commit=[&](){auto a=Now(),b=Now();done.Flushed(a,b);};
 for(uint64_t p=0;p<2;++p){uint64_t reads=0;
  for(uint64_t n=0;n<240;++n){one(true,16+n);if((n+1)%32==0)commit();
   while(reads<(n+1)*128/240)one(false,(p*128+reads++)%16);}
  commit();}
 done.Finish(736);trace.Finish();return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cc').write_text(code)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'tests'),str(p/'test.cc'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test'),'cancel'],check=True,timeout=5)
            env=dict(os.environ);env['RRZC_END_TO_END']=str(p/'应用小测完成.bin')
            with (p/'计时客户端小测.log').open('w') as f:
                subprocess.run([str(p/'test'),str(p/'计时小测请求.bin'),'ledger'],env=env,
                    stdout=f,stderr=subprocess.STDOUT,check=True,timeout=10)
            result=analyze(p,smoke=True)
            self.assertEqual(result['完成请求数'],736)
            self.assertEqual(result['实际同步写批次'],16)
            self.assertTrue(all(r['平均毫秒']>=r['平均API毫秒'] for r in result['指标']))
            file=p/'应用小测完成.bin';raw=bytearray(file.read_bytes())
            for off in range(24,len(raw),80):
                r=struct.unpack_from('<10Q',raw,off)
                if r[1]:
                    struct.pack_into('<Q',raw,off+8*8,r[7]);break
            file.write_bytes(raw)
            with self.assertRaises(ValueError):analyze(p,smoke=True)

    def test_source_waits_for_real_flush_and_retains_operations(self):
        root=PRIOR/PRIOR.name.removesuffix('-results')
        old=(root/'校准-db_bench_tool.cc').read_text();new=end_to_end_source.client(old)
        method=new.split('  void RRZCSstLifetime(',1)[1].split('  void RRZCRotating(',1)[0]
        self.assertIn('flush.wait=true;',method)
        a=method.index('auto status=db_.db->Flush(flush);')
        b=method.index('if(!status.ok())throw std::runtime_error(status.ToString());',a)
        c=method.index('application.Flushed(flush_begin,flush_end);')
        self.assertLess(a,b);self.assertLess(b,c)
        self.assertIn('requests.Take(trace.Count(),false,id)',method)
        self.assertIn('requests.Take(trace.Count(),true,id)',method)
        self.assertIn('smoke?64:4096',method)
        for token in ('auto status=db_.db->Get(options,key,&value);',
                      'auto status=db_.db->Put(write_options_,key,value);'):
            self.assertEqual(new.count(token),old.count(token))


if __name__=='__main__':unittest.main()
