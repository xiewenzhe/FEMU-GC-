"""仅延长原请求前缀并增加共同观测；不改变设备、维护对象或选区。"""
from calibration_source import once

WINDOWS=24
READS=1572864
WRITES=2949120
REQUESTS=READS+WRITES


def fs_header(text):
    return once(text,'  void GetZenFSSnapshot(ZenFSSnapshot& snapshot,',
        '  uint64_t ObserveStreamLog(uint64_t stage,uint64_t operations);\n\n  void GetZenFSSnapshot(ZenFSSnapshot& snapshot,')


def fs_source(text):
    anchor='void ZenFS::GetZenFSSnapshot('
    return once(text,anchor,'''// 仅供显式受控客户端观测；不访问文件数据、不改变维护规则。
uint64_t ZenFS::ObserveStreamLog(uint64_t stage,uint64_t operations) {
  if(stage)Info(logger_,"持续负载维护边界：检查点=%lu，请求=%lu",stage,operations);
  logger_->Flush();
  const auto size=logger_->GetLogFileSize();
  if(size==size_t(-1))throw std::runtime_error("当前Logger不支持日志长度观测");
  return static_cast<uint64_t>(size);
}

'''+anchor)


def client(text):
    text=once(text,'#include "rrzc_sst_lifetime.h"',
        '#include "rrzc_sst_lifetime.h"\n#include "rrzc_stream_phase.h"')
    text=once(text,'    const auto plan=rrzc_sst_lifetime::Plan::Make(smoke==1,persistent==1);',
        '''    auto plan=rrzc_sst_lifetime::Plan::Make(smoke==1,persistent==1);
    const auto stream_windows=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_WINDOWS",plan.windows);
    if(smoke) {
      if(stream_windows!=1)throw std::runtime_error("小测窗口数必须保持1");
    } else if(stream_windows!=12 && stream_windows!=24) {
      throw std::runtime_error("持续实验只接受原12或扩展24窗口");
    }
    if(stream_windows==24 && (!persistent || !interleaved || FLAGS_fs_uri.find("zenfs://")!=0))
      throw std::runtime_error("24窗口观测仅支持固定H交错的ZenFS实验入口");
    plan.windows=stream_windows;
    fprintf(stderr,"持续负载窗口：数量=%llu，原请求前缀保留=1\\n",(unsigned long long)plan.windows);''')
    return once(text,'      fprintf(stderr,"SST生命周期阶段：序号=',
        '''      if(stream_windows==24 && stage%60==0)rrzc_stream_phase::Capture(FLAGS_env->GetFileSystem().get(),
          getenv("RRZC_CAL_REQUEST_TRACE"),stage,(stage/10),trace.Count());
      fprintf(stderr,"SST生命周期阶段：序号=''')


def wrapper(text):
    text=once(text,'ZENFS_RRZC_POOL_TAIL_OBSERVE=1',
        'RRZC_SST_LIFETIME_WINDOWS=24 ZENFS_RRZC_POOL_TAIL_OBSERVE=1')
    return once(text,'--reads=786432 --writes=1474560',
        '--reads=1572864 --writes=2949120')
