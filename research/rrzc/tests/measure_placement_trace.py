"""只测主机单线程Profile函数开销；不能代表ZenFS端到端观测开销或业务收益。"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
from analyze_placement_trace import rows

CODE=r'''#include "原热度.h"
#include "rrzc_stream_profile.h"
#include <chrono>
#include <iostream>
#include <string>
template<class Profile> void Measure(void(*tick)()) {
  Profile profile;uint64_t sum=0;
  const auto start=std::chrono::steady_clock::now();
  for(unsigned i=0;i<400000;++i) {
    if(i%11==0)tick();
    else {const auto s=profile.Read((i%19)*4096,4096,65536);sum+=s.reads+s.reused+s.bytes+s.hot;}
  }
  const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
  std::cout<<"{\"纳秒\":"<<ns<<",\"摘要\":"<<sum<<",\"循环\":400000}\n";
}
int main(int argc,char** argv) {
  if(argc!=2)return 2;
  if(std::string(argv[1])=="old")Measure<old_stream::Profile>([]{old_stream::Tick();});
  else Measure<rrzc_stream::Profile>([]{rrzc_stream::Tick();});
}'''


def measure(source,output):
    assert not output.exists(),'不覆盖开销记录'
    source=source.resolve()
    origin=Path(json.loads((source/'副本身份.json').read_text())['来源'])
    old=origin/'新版源码/rrzc_stream_profile.h'
    env={k:v for k,v in os.environ.items() if not k.startswith('ZENFS_RRZC_')}
    env['ZENFS_RRZC_PLACEMENT_MODE']='G2'
    results=[]
    with tempfile.TemporaryDirectory(prefix='rrzc-observer-cost-') as name:
        work=Path(name)
        (work/'原热度.h').write_text(old.read_text().replace('namespace rrzc_stream {','namespace old_stream {'))
        (work/'开销.cc').write_text(CODE)
        command=['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
            '-I'+str(source/'新版源码'),'-I'+str(work),str(work/'开销.cc'),'-o',str(work/'开销')]
        subprocess.run(command,check=True,timeout=30)
        for repeat in range(5):
            order=['旧Profile','新观测关闭','新观测开启'];order=order[repeat%3:]+order[:repeat%3]
            for kind in order:
                active=dict(env);path=work/(str(repeat)+'.bin')
                if kind=='新观测开启':active['ZENFS_RRZC_PLACEMENT_TRACE']=str(path)
                result=subprocess.run([str(work/'开销'),'old' if kind=='旧Profile' else 'new'],
                    env=active,capture_output=True,text=True,check=True,timeout=30)
                item=json.loads(result.stdout);item.update(轮=repeat+1,版本=kind)
                if path.exists():
                    records=list(rows(path));item['事件数']=len(records);item['事件文件字节']=path.stat().st_size
                    path.unlink()
                results.append(item)
    assert len({r['摘要'] for r in results})==1,'观测修改了分类结果'
    medians={k:statistics.median(r['纳秒'] for r in results if r['版本']==k)
        for k in ['旧Profile','新观测关闭','新观测开启']}
    report=dict(限定='主机单线程合成Profile调用，五轮串行、40万事件；不含文件映射、观测全局锁竞争、'
        '存盘或SSD/RocksDB业务，不能用来推断整体IO性能。',编译参数=command,原始记录=results,
        中位纳秒=medians,相对旧Profile={k:v/medians['旧Profile'] for k,v in medians.items()})
    with output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(measure(a.source,a.output),ensure_ascii=False,indent=2))
