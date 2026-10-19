// 测试补丁内真实查询函数；ioctl是内存替身，不访问NVMe设备。
#include "../model/rrzc_recent_log.h"
#include <linux/nvme_ioctl.h>
#include <sys/ioctl.h>
#include <cerrno>
#include <cassert>
#include <cstring>
#include <iostream>

struct IOStatus {
  std::string message;
  bool ok() const { return message.empty(); }
  static IOStatus OK() { return {}; }
  static IOStatus InvalidArgument(std::string s) { return {s}; }
  static IOStatus NotSupported(std::string s) { return {s}; }
  static IOStatus IOError(std::string s) { return {s}; }
  static IOStatus Corruption(std::string s) { return {s}; }
};
struct ZbdlibBackend {
  uint32_t nr_zones_=2;
  uint64_t zone_sz_=256ULL<<20;
  int read_f_=17;
  IOStatus GetZoneRecentReads(rrzc_v1::RecentReport*);
};
static unsigned checks,queries;
static int nsid=1,admin_result;
static bool malformed;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static void Put(uint8_t* p,uint64_t value,size_t bytes) {
  for (size_t i=0; i<bytes; ++i) p[i]=uint8_t(value>>(8*i));
}
static int FakeIoctl(int fd,unsigned long op) {
  CHECK(fd==17 && op==NVME_IOCTL_ID); return nsid;
}
static int FakeIoctl(int fd,unsigned long op,nvme_admin_cmd* cmd) {
  ++queries; CHECK(fd==17 && op==NVME_IOCTL_ADMIN_CMD);
  CHECK(cmd->opcode==2 && cmd->nsid==1);
  CHECK((cmd->cdw10&0xffff)==0xc2 && (cmd->cdw10>>16)==57);
  CHECK(cmd->cdw11==0 && cmd->cdw12==0 && cmd->cdw13==0);
  CHECK(cmd->data_len==232);
  if (admin_result) { errno=EIO; return admin_result; }
  auto p=reinterpret_cast<uint8_t*>(cmd->addr);
  std::memset(p,0,232);
  Put(p,malformed ? 2 : 1,4); Put(p+4,88,4); Put(p+8,2,4); Put(p+12,64,4);
  Put(p+16,4096,8); Put(p+24,42,8);
  Put(p+40,10*rrzc_v1::kSecond,8); Put(p+48,30*rrzc_v1::kSecond,8);
  for (unsigned i=0; i<2; ++i) {
    uint8_t* b=p+56+i*88;
    Put(b,i*65536,8); Put(b+8,i,8);
    Put(b+16,40*rrzc_v1::kSecond,8); Put(b+24,rrzc_v1::kSecond,8);
    Put(b+32,2,8); Put(b+40,1,8); Put(b+48,1,8);
    Put(b+56,i,4); Put(b+60,4+i,4); Put(b+80,7,4);
  }
  return 0;
}
#define ioctl FakeIoctl
#include "rr-recent-backend-under-test.inc"
#undef ioctl
int main() {
  ZbdlibBackend backend; rrzc_v1::RecentReport report;
  CHECK(backend.GetZoneRecentReads(&report).ok()); CHECK(queries==1);
  CHECK(report.zones.size()==2 && report.zones[1].zone==(256ULL<<20));
  CHECK(report.zones[1].block_id==5 && report.zones[1].generation==1);
  malformed=true; CHECK(!backend.GetZoneRecentReads(&report).ok());
  CHECK(queries==2 && report.zones.empty()); malformed=false;
  for (int ret:{-1,9}) {
    admin_result=ret; CHECK(!backend.GetZoneRecentReads(&report).ok());
    CHECK(report.zones.empty());
  }
  admin_result=0; nsid=0; auto count=queries;
  CHECK(!backend.GetZoneRecentReads(&report).ok()); CHECK(queries==count);
  nsid=1; backend.nr_zones_=0;
  CHECK(!backend.GetZoneRecentReads(&report).ok()); CHECK(queries==count);
  backend.nr_zones_=UINT32_MAX;
  CHECK(!backend.GetZoneRecentReads(&report).ok()); CHECK(queries==count);
  std::cout<<"ZenFS生产C2h查询内存联调："<<checks<<"项检查通过（测试替身）\n";
}
