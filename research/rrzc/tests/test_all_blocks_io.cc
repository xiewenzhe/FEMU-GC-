// 复用合成协议帧；ioctl替身核验真实主机查询代码的LSI和传输参数。
#define main archived_all_blocks_unit_main
#include "test_all_blocks.cc"
#undef main
#include <linux/nvme_ioctl.h>
#include <linux/fs.h>
#include <sys/ioctl.h>
#include <cstdarg>
#include <cstring>
static int result_code;
static int FakeIoctl(int fd,unsigned long request,...) {
  assert(fd==17);
  if(request==NVME_IOCTL_ID)return 3;
  va_list args;va_start(args,request);
  if(request==BLKSSZGET){*va_arg(args,unsigned*)=512;va_end(args);return 0;}
  auto* cmd=va_arg(args,nvme_admin_cmd*);va_end(args);
  assert(request==NVME_IOCTL_ADMIN_CMD && cmd->opcode==2 && cmd->nsid==3);
  assert(cmd->data_len==232 && cmd->cdw10==((57U<<16)|0xc4) && cmd->cdw11==(5U<<16));
  assert(!cmd->cdw12 && !cmd->cdw13);
  if(result_code)return result_code;
  const auto bytes=Wire(Frame(40,980,500));std::memcpy(reinterpret_cast<void*>(cmd->addr),bytes.data(),bytes.size());
  return 0;
}
#define ioctl FakeIoctl
#include "rrzc_all_blocks_io.h"
#undef ioctl
int main() {
  Report report;std::string error;
  assert(Query(17,3,64,2,5,128ULL<<20,512,&report,&error));
  assert(report.blocks.size()==2 && report.blocks[1].rc==500);
  result_code=9;assert(!Query(17,3,64,2,5,128ULL<<20,512,&report,&error));
  assert(report.blocks.empty() && error.find("NVMe")!=std::string::npos);
  assert(!Query(17,3,64,2,64,128ULL<<20,512,&report,&error));
  assert(!Query(17,3,64,16385,5,128ULL<<20,512,&report,&error));
  std::cout<<"主机C4h真实查询：LSI、NUMD、单帧、设备错误和空间上界通过（ioctl替身）\n";
}
