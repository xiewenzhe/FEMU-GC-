"""对已经安装严格RR的实测源码做单次、可逆的最小接线。"""
import difflib
from pathlib import Path


def replace_once(text,before,after):
    if text.count(before)!=1:raise ValueError('严格RR补丁父源结构不符：'+before[:90])
    return text.replace(before,after,1)


def patched(name,text):
    if name=='io_zenfs.cc':
        text=replace_once(text,'IOStatus ZoneFile::PositionedRead(uint64_t offset, size_t n, Slice* result,',
            '#include "rrzc_strict_wait.h"\n\nIOStatus ZoneFile::PositionedRead(uint64_t offset, size_t n, Slice* result,')
        start=text.index('  auto strict_lock=zbd_->StrictLock();',text.index('IOStatus ZoneFile::PositionedRead('))
        end=text.index('\n}\n',start)
        text=text[:start]+'''  if (!zbd_->StrictRR()) return PositionedReadOnce(offset,n,result,scratch,direct);
  return rrzc_strict::ReadWithMaintenance(
      [&]() {return PositionedReadOnce(offset,n,result,scratch,direct);},
      [&](bool* complete) {return zbd_->ServiceStrictRR(complete);},
      [&]() {return zbd_->StrictLock();},
      []() {return IOStatus::IOError("严格RR等待覆盖完整的源超时，拒绝继续到期块读取");});'''+text[end:]
        # 头文件必须在命名空间外，避免把标准库声明放进ROCKSDB_NAMESPACE。
        text=replace_once(text,'#include "rrzc_strict_wait.h"\n\n','')
        text=replace_once(text,'#include "io_zenfs.h"','#include "io_zenfs.h"\n#include "rrzc_strict_wait.h"')
    elif name=='fs_zenfs.cc':
        start=text.index('    zbd_->SetStrictRRHandler([this]() {')
        end=text.index('\n    });',start)+len('\n    });')
        text=text[:start]+'''    zbd_->SetStrictRRHandler([this](bool* complete) {
      *complete=false;
      // 调用方持严格读锁；一次只做一轮维护，未就绪时返回并释放锁。
      auto pending=[this](bool* found) {
        *found=false;
        ZoneReadPressureReport pressure;
        const auto status=zbd_->GetZoneReadPressure(&pressure);
        if (!status.ok()) return status;
        for (const auto& p:pressure.zones) {
          const auto* zone=zbd_->GetIOZone(p.start);
          if (zone && zone->used_capacity_ && p.blocks_at_threshold) *found=true;
        }
        return IOStatus::OK();
      };
      bool found=false;
      auto status=pending(&found);
      if (!status.ok()) return status;
      if (!found) {*complete=true;return status;}
      status=ReadReclaimOneZone();
      if (!status.ok()) return status;
      status=pending(&found);
      if (!status.ok()) return status;
      *complete=!found;
      return IOStatus::OK();
    });'''+text[end:]
        text=replace_once(text,'严格RR主机配置：启用=1，阈值完成立即同步服务=1，先释放文件读锁=1，旧GC周期不变=1',
            '严格RR主机配置：启用=1，阈值完成立即同步服务=1，先释放文件读锁=1，旧GC周期不变=1，覆盖等待释放全局读锁=1')
    elif name=='zbd_zenfs.h':
        text=replace_once(text,'std::function<IOStatus()> strict_rr_handler_',
            'std::function<IOStatus(bool*)> strict_rr_handler_')
        text=replace_once(text,'SetStrictRRHandler(std::function<IOStatus()> handler)',
            'SetStrictRRHandler(std::function<IOStatus(bool*)> handler)')
        text=replace_once(text,'IOStatus ServiceStrictRR() {','IOStatus ServiceStrictRR(bool* complete) {')
        text=replace_once(text,'strict_rr_handler_() :','strict_rr_handler_(complete) :')
    else:raise ValueError('不支持的补丁目标')
    return text


def make_patch(source):
    result=''
    for name in ('io_zenfs.cc','fs_zenfs.cc','zbd_zenfs.h'):
        old=(Path(source)/name).read_text()
        result+=''.join(difflib.unified_diff(old.splitlines(True),patched(name,old).splitlines(True),
            fromfile='a/fs/'+name,tofile='b/fs/'+name))
    return result
