"""只向完整机制副本增加默认关闭的零有效尾部复用，选区和RR正文不改。"""
from calibration_source import once


def header(text):
    text=once(text,'#pragma once','#pragma once\n#include "rrzc_pool_tail.h"')
    text=once(text,'  void InitColocationPool();','''  std::mutex pool_tail_mutex_;
  std::set<uint64_t> pool_tail_held_;
  bool KeepPoolTail(Zone*);
  void InitColocationPool();''')
    return once(text,'  IOStatus RestoreColocation(','''  bool ForgetPoolTail(uint64_t);
  IOStatus ReleasePoolTails(bool,bool*,const char*);
  IOStatus RestoreColocation(''')


def zbd(text):
    text=once(text,'  if(zbd_->ColocationPool())zbd_->ColocationPool()->Reset(start_);',
        '  if(zbd_->ColocationPool())zbd_->ColocationPool()->Reset(start_);\n  zbd_->ForgetPoolTail(start_);')
    text=once(text,'        bool full = z->IsFull();','''        if(KeepPoolTail(z)) {
          const auto release=z->CheckRelease();if(!release.ok())return release;
          continue;
        }
        ForgetPoolTail(z->start_);
        bool full = z->IsFull();''')
    text=once(text,'IOStatus ZonedBlockDevice::FinishCheapestIOZone() {\n  IOStatus s;',
        '''IOStatus ZonedBlockDevice::FinishCheapestIOZone() {
  bool released=false;
  auto tail_status=ReleasePoolTails(false,&released,"普通Active不足");
  if(!tail_status.ok() || released)return tail_status;
  IOStatus s;''')
    # 原空区分配函数入口尚未持有预留锁，先尝试回收被保留但无有效数据的池区。
    anchor='IOStatus ZonedBlockDevice::AllocateEmptyZone('
    start=text.index(anchor);brace=text.index('{',start)
    text=text[:brace+1]+'''
  bool released_tail=false;
  auto tail_status=ReleasePoolTails(true,&released_tail,"没有普通空Zone");
  if(!tail_status.ok())return tail_status;
'''+text[brace+1:]
    return text


def adapter(text):
    text=once(text,'void ZonedBlockDevice::InitColocationPool() {',
        '#include "rrzc_pool_tail_zenfs.inc"\n\nvoid ZonedBlockDevice::InitColocationPool() {')
    text=once(text,'  const char* k=getenv("ZENFS_RRZC_POOL_K");',
        '''  if(rrzc_pool_tail::Observe())Info(logger_,"协同尾部配置：版本=1，执行=%d，观测=1，最小尾部=设备块对齐，资源压力退出=1，关闭前清账=1",rrzc_pool_tail::Enabled());
  const char* k=getenv("ZENFS_RRZC_POOL_K");''')
    return once(text,'  *output=z;return IOStatus::OK();','''  if(ForgetPoolTail(z->start_))Info(logger_,"协同尾部复用：Zone=%lu，WP=%lu，剩余=%lu，有效=%lu，RC不清零=1，沿原WP追加=1",z->start_,z->wp_,z->capacity_,z->used_capacity_.load());
  *output=z;return IOStatus::OK();''')


def fs(text):
    return once(text,'  meta_log_.reset(nullptr);\n  ClearFiles();','''  if(rrzc_pool_tail::Observe()) {
    bool released=false;
    const auto cleanup=zbd_->ReleasePoolTails(false,&released,"正常关闭清账");
    Info(logger_,"协同尾部关闭清账：成功=%d，有释放=%d，状态=%s",cleanup.ok(),released,cleanup.ToString().c_str());
  }
  meta_log_.reset(nullptr);
  ClearFiles();''')
