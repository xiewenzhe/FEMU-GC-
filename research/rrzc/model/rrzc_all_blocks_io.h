// Linux独立只读查询，可由ZenFS或受控工具复用；不修改原后端虚函数表。
#pragma once
#include "rrzc_all_blocks.h"
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <linux/nvme_ioctl.h>
#include <linux/fs.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace rrzc_all_blocks {
inline bool Query(int fd,uint32_t nsid,uint32_t zones,uint32_t count,uint32_t index,
                  uint64_t zone_size,uint32_t lba_size,Report* output,std::string* error) {
  *output={};
  if(fd<0 || !nsid || !count || index>=zones || index>65535 ||
      count>(UINT32_MAX-56)/88 || count>16384) {
    *error="C4h查询参数无效或单帧超过保护上限";return false;
  }
  std::vector<uint8_t> bytes(56+size_t(count)*88);nvme_admin_cmd cmd{};
  cmd.opcode=2;cmd.nsid=nsid;cmd.addr=reinterpret_cast<uint64_t>(bytes.data());
  cmd.data_len=bytes.size();const uint32_t numd=cmd.data_len/4-1;
  cmd.cdw10=((numd&65535)<<16)|0xc4;cmd.cdw11=(index<<16)|(numd>>16);
  const int result=ioctl(fd,NVME_IOCTL_ADMIN_CMD,&cmd);
  if(result!=0){*error=result<0?"C4h读取失败："+std::string(std::strerror(errno)):
      "C4h设备返回NVMe状态："+std::to_string(result);return false;}
  return Decode(bytes.data(),bytes.size(),zones,count,index,zone_size,lba_size,output,error);
}
inline bool QueryPath(const std::string& path,uint32_t zones,uint32_t count,uint32_t index,
                      uint64_t zone_size,Report* output,std::string* error) {
  *output={};const int fd=open(path.c_str(),O_RDONLY|O_CLOEXEC);
  if(fd<0){*error="C4h无法打开设备";return false;}
  const int nsid=ioctl(fd,NVME_IOCTL_ID);unsigned lba=0;
  // BLKSSZGET得到LBA大小，不能把ZenFS对齐块大小误当NVMe LBA大小。
  const bool valid=nsid>0 && ioctl(fd,BLKSSZGET,&lba)==0;
  const bool ok=valid && Query(fd,nsid,zones,count,index,zone_size,lba,output,error);
  if(!valid)*error="C4h无法确认命名空间或LBA大小";
  close(fd);return ok;
}
}  // 只读查询命名空间结束。
