#!/usr/bin/env python3
"""在已验收受控客户端追加相同写入与冷文件退役分支，不修改模型。"""
from pathlib import Path
from all_blocks_patch import once

HELPER=r'''
// 此处只安排业务操作；不直接调用GC、RR、Finish、Reset或改变选择输入。
static void followup(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                     const std::string& out,const FileOptions& options,Buffer& data,
                     const uint64_t sources[2]) {
  const char* raw=getenv("RRZC_FOLLOWUP_BRANCH");
  const std::string branch=raw?raw:"";
  require(branch=="keep" || branch=="retire","未指定后续存活分支");
  std::ofstream phases(out+"/阶段空间.csv");
  phases<<"阶段,追加逻辑字节,空闲字节,有效字节,可回收字节,冷文件存在,冷映射Zone,原冷源Empty,原冷源设备代际,阈值触发数,最高RC\n";
  auto record=[&](const char* stage,uint64_t appended) {
    const auto cold=find(fs,name(0));const auto source=all(d,sources[0]);const auto counts=strict_stats();
    phases<<stage<<','<<appended<<','<<d.GetFreeSpace()<<','<<d.GetUsedSpace()<<','<<d.GetReclaimableSpace()<<','
      <<bool(cold)<<','<<(cold?cold->zone_start:UINT64_MAX)<<','<<d.GetIOZone(sources[0])->IsEmpty()<<','
      <<source.blocks[0].generation<<','<<counts[6]<<','<<counts[8]<<'\n';
    phases.flush();require(phases.good(),"阶段空间记录失败");
    Info(logger,"追加阶段边界：阶段=%s，分支=%s，追加逻辑字节=%lu，冷存在=%d，冷映射=%lu",
        stage,branch.c_str(),appended,bool(cold),cold?cold->zone_start:UINT64_MAX);logger->Flush();
  };
  record("初始窗口结束",0);
  // 两组均在同一业务位置执行同样退役；未来删除不作为选区模型输入。
  if(branch=="retire")check(fs.DeleteFile(name(0),IOOptions(),nullptr));
  record("存活条件执行后",0);
  for(unsigned i=0;i<4;++i) {
    const auto path="/v1-execution-smoke/append"+std::to_string(i)+".bin";
    write(fs,path,32*MiB,200+i,Env::WLTH_MEDIUM,data,options);
    Info(logger,"追加业务写入：序号=%u，文件=%s，逻辑字节=%lu",i,path.c_str(),32*MiB);
    const auto stage="追加写入"+std::to_string(i+1);record(stage.c_str(),uint64_t(i+1)*32*MiB);
  }
  // 只等待原10秒维护检查；不强制某组补GC，不按结果改变追加字节数。
  std::this_thread::sleep_for(std::chrono::seconds(12));
  record("追加观察结束",128*MiB);
}
static void verify_appended(ZenFS& fs,const FileOptions& options,Buffer& expected,Buffer& scratch) {
  for(unsigned i=0;i<4;++i) {
    const auto path="/v1-execution-smoke/append"+std::to_string(i)+".bin";
    uint64_t bytes=0;check(fs.GetFileSize(path,IOOptions(),&bytes,nullptr));require(bytes==32*MiB,"追加文件长度不符");
    expected.fill(200+i);std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(path,options,&reader,nullptr));
    for(uint64_t pos=0;pos<bytes;pos+=MiB) {
      Slice result;check(reader->Read(pos,MiB,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==MiB && !std::memcmp(result.data(),expected.get(),MiB),"追加文件完整内容不符");
    }
  }
}
'''

def client_source(text):
    text=once(text,'static void run_case(',HELPER+'\nstatic void run_case(')
    text=once(text,'  std::ofstream final(out+"/结束空间.csv");',
        '  followup(fs,d,logger,out,options,data,sources);\n  std::ofstream final(out+"/结束空间.csv");')
    text=once(text,'    expected.fill(i+1);const auto size=i?valid_size(scenario):4*MiB;',
        '''    if(!i && getenv("RRZC_FOLLOWUP_BRANCH") && !strcmp(getenv("RRZC_FOLLOWUP_BRANCH"),"retire")) {
      require(fs.FileExists(name(0),IOOptions(),nullptr).IsNotFound(),"预定退役冷文件仍存在");continue;
    }
    expected.fill(i+1);const auto size=i?valid_size(scenario):4*MiB;''')
    text=once(text,'  verify_candidates(fs,scenario);std::cout',
        '  verify_candidates(fs,scenario);verify_appended(fs,options,data,scratch);std::cout')
    return text

def wrapper_source(text):
    text=once(text,'[[ $scenario == opportunity || $scenario == stopped || $scenario == low-yield ]]',
        '[[ $scenario == keep || $scenario == retire ]]')
    text=once(text,'[[ $mode == greedy || $mode == current || $mode == blocks ]]',
        '[[ $mode == greedy || $mode == blocks ]]')
    text=once(text,'env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 \\\n',
        'env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 RRZC_FOLLOWUP_BRANCH="$scenario" \\\n')
    return once(text,'"$target/all_blocks_device" "$scenario" "$result"',
        '"$target/all_blocks_device" opportunity "$result"')

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('tests',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.mkdir(exist_ok=True)
    for name,text in (('all_blocks_device.cc',client_source((a.tests/'all_blocks_device.cc').read_text())),
                      ('run_all_blocks_guest.sh',wrapper_source((a.tests/'run_all_blocks_guest.sh').read_text()))):
        with (a.output/name).open('x') as f:f.write(text)

if __name__=='__main__':main()
