/* 提取生产函数进行内存联调；设备、DMA、时钟为测试替身，不是虚拟机实验。 */
#include <assert.h>
#include <endian.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../../hw/femu/zns/rr-history.h"
#include "../../../hw/femu/zns/rr-strict.h"

#define QEMU_PACKED __attribute__((packed))
#define QEMU_BUILD_BUG_ON(x) _Static_assert(!(x), "协议结构长度异常")
#define NVME_INVALID_LOG_ID 9
#define NVME_INVALID_FIELD 2
#define NVME_DNR 0x4000
#define QEMU_CLOCK_REALTIME 0
#define LOGICAL_PAGE_SIZE 4096
#define USER_IO 1
#define NAND_READ 1
#define FEMU_ZNS_RR_READ 0xc2
#define FEMU_ZNS_RR_MARKER 0x52520000u
#define FEMU_ZNS_RR_PENDING 0x07f0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define cpu_to_le32(x) htole32(x)
#define cpu_to_le64(x) htole64(x)
#define le64_to_cpu(x) le64toh(x)
#define le32_to_cpu(x) le32toh(x)
#define qatomic_read(p) __atomic_load_n((p), __ATOMIC_SEQ_CST)
#define qatomic_set(p,v) __atomic_store_n((p),(v), __ATOMIC_SEQ_CST)
#define qatomic_inc(p) __atomic_fetch_add((p),1,__ATOMIC_SEQ_CST)
#define qemu_mutex_lock pthread_mutex_lock
#define qemu_mutex_unlock pthread_mutex_unlock
#define g_malloc0(n) calloc(1,(n))
#define g_free free
#define ftl_debug(...) ((void)0)
#define ftl_err(...) ((void)0)
typedef pthread_mutex_t QemuMutex;
#include "rr-recent-wire-under-test.inc"
typedef struct ZnsRrZoneHistory {
  QemuMutex lock;
  uint64_t generation,born_ns;
  FemuRrHistory* blocks;
} ZnsRrZoneHistory;
struct ppa {
  uint64_t ppa;
  struct { uint32_t ch,fc,pl,blk,pg,spg; } g;
};
struct zns_blk { uint64_t read_count,page_wp; };
struct zns_ssd {
  uint64_t num_ch,num_lun,num_plane,chnls_per_zone,lbasz;
  bool rr_history_enabled;
  /* 补齐当前生产读取函数使用的统计字段；旧C2功能用例保持严格模式关闭。 */
  bool rr_strict;
  uint64_t rr_blocked_reads,rr_actual_reads,rr_self_reads,rr_counted_reads;
  uint64_t rr_other_migration_reads,rr_trigger_events,rr_peak_count;
  uint64_t rr_threshold,rr_session_hi,rr_session_lo;
  uint32_t rr_history_max_events;
  ZnsRrZoneHistory* rr_history;
  uint64_t* zone_wp_slot;
  struct { struct { struct { struct zns_blk blk[2]; } plane[2]; } fc[2]; } ch[4];
  struct ppa mapping[2];
};
typedef struct NvmeNamespace {
  struct zns_ssd* zns;
  uint32_t num_zones;
  struct { struct { uint64_t zslba; } d; } zone_array[8];
} NvmeNamespace;
typedef struct FemuCtrl { unsigned unused; } FemuCtrl;
typedef struct NvmeCmd { struct { uint64_t prp1,prp2; } dptr; } NvmeCmd;
typedef struct NvmeRequest {
  uint64_t slba,stime; uint32_t nlb; uint16_t status;
  struct {uint8_t opcode;uint32_t cdw13;} cmd;
  struct {struct {uint32_t result;} n;} cqe;
} NvmeRequest;
struct nand_cmd { int type,cmd; uint64_t stime; };
static uint64_t test_ns;
static uint64_t qemu_clock_get_ns(int unused) {
  (void)unused; return qatomic_read(&test_ns);
}
static bool valid_ppa(struct zns_ssd* s,struct ppa* p) {
  return p->g.ch<s->num_ch && p->g.fc<s->num_lun &&
         p->g.pl<s->num_plane && p->g.blk<2;
}
static bool mapped_ppa(struct ppa* p) { return p->ppa!=UINT64_MAX; }
static struct zns_blk* get_blk(struct zns_ssd* s,struct ppa* p) {
  return &s->ch[p->g.ch].fc[p->g.fc].plane[p->g.pl].blk[p->g.blk];
}
static struct ppa get_maptbl_ent(struct zns_ssd* s,uint64_t lpn) {
  assert(lpn<2); return s->mapping[lpn];
}
static uint64_t zns_advance_status(struct zns_ssd* s,struct ppa* p,struct nand_cmd* c) {
  (void)s; (void)p; (void)c; return 7;
}
static uint16_t dma_read_prp(FemuCtrl* n,void* data,size_t len,uint64_t p1,uint64_t p2) {
  (void)n; (void)p2; memcpy((void*)(uintptr_t)p1,data,len); return 0;
}
#include "rr-recent-device-under-test.inc"
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
typedef struct Fixture {
  struct zns_ssd s;
  NvmeNamespace ns;
  ZnsRrZoneHistory history[8];
  FemuRrHistory blocks[8][16];
  uint64_t slots[8];
} Fixture;
static void Init(Fixture* f,uint32_t cpz) {
  memset(f,0,sizeof(*f));
  f->s.num_ch=4; f->s.chnls_per_zone=cpz;
  f->s.num_lun=f->s.num_plane=2; f->s.lbasz=512;
  f->s.rr_threshold=4096; f->s.rr_history_enabled=true;
  f->s.rr_session_hi=42; f->s.rr_history_max_events=128;
  f->s.rr_history=f->history; f->s.zone_wp_slot=f->slots;
  f->ns.zns=&f->s; f->ns.num_zones=2*(4/cpz);
  for (unsigned z=0; z<f->ns.num_zones; ++z) {
    pthread_mutex_init(&f->history[z].lock,NULL);
    f->history[z].blocks=f->blocks[z];
    f->ns.zone_array[z].d.zslba=z*65536;
  }
  f->s.mapping[1].ppa=UINT64_MAX;
}
static void Destroy(Fixture* f) {
  for (unsigned z=0; z<f->ns.num_zones; ++z) {
    for (unsigned b=0; b<f->s.chnls_per_zone*4; ++b)
      femu_rr_history_destroy(&f->blocks[z][b]);
    pthread_mutex_destroy(&f->history[z].lock);
  }
}
static void Read(Fixture* f,uint32_t ch,uint32_t lun,uint32_t plane,uint32_t block) {
  f->s.mapping[0]=(struct ppa){.g={.ch=ch,.fc=lun,.pl=plane,.blk=block}};
  NvmeRequest req={.nlb=8};
  CHECK(zns_read(&f->s,&req)==7);
}
static uint16_t Query(Fixture* f,uint8_t* data,uint32_t len,uint64_t off) {
  FemuCtrl ctrl={0}; NvmeCmd cmd={.dptr={.prp1=(uintptr_t)data}};
  return zns_rr_recent_log(&ctrl,&f->ns,&cmd,len,off);
}
int main(void) {
  const uint64_t sec=FEMU_RR_SECOND_NS;
  uint8_t bytes[56+8*88];
  for (uint32_t cpz=1; cpz<=4; cpz*=2) {
    Fixture f; Init(&f,cpz); qatomic_set(&test_ns,sec);
    uint32_t z=4/cpz, ch=cpz-1, id=(cpz-1)*4+3;
    Read(&f,ch,1,1,1);
    qatomic_set(&test_ns,20*sec); Read(&f,ch,1,1,1);
    qatomic_set(&test_ns,31*sec);
    CHECK(Query(&f,bytes,sizeof(bytes),0)==0);
    ZnsRrRecentHeader* header=(void*)bytes;
    ZnsRrRecentEntry* rows=(void*)(bytes+56);
    CHECK(le32toh(header->nr_zones)==f.ns.num_zones);
    CHECK(le32toh(header->blocks_per_zone)==cpz*4);
    CHECK(le64toh(rows[z].current_rc)==2 && le32toh(rows[z].block_id)==id);
    CHECK(le64toh(rows[z].delta10)==0 && le64toh(rows[z].delta30)==1);
    CHECK(le32toh(rows[z].flags)==7);
    CHECK(le32toh(rows[z].ch)==ch && le32toh(rows[z].lun)==1 &&
          le32toh(rows[z].plane)==1 && le32toh(rows[z].block)==1);
    // 改成另一个最大块，应返回该块自己的3个事件，而不是最大值差1。
    for (unsigned i=0; i<3; ++i) Read(&f,0,0,0,1);
    CHECK(Query(&f,bytes,sizeof(bytes),0)==0);
    CHECK(le32toh(rows[z].block_id)==0 && le64toh(rows[z].delta10)==3);
    CHECK(le64toh(rows[z].current_rc)==3 && le64toh(rows[z].delta30)==3);
    Read(&f,0,0,0,0);
    zns_reset_block_state(&f.s,z);
    CHECK(Query(&f,bytes,sizeof(bytes),0)==0);
    CHECK(le64toh(rows[z].generation)==1 && le64toh(rows[z].current_rc)==0);
    CHECK(le64toh(rows[z].delta10)==0 && le64toh(rows[z].delta30)==0);
    CHECK(le32toh(rows[z].flags)==1 && le64toh(rows[z].epoch_start_ns)==31*sec);
    CHECK(le64toh(rows[0].current_rc)==1 && le64toh(rows[0].generation)==0);
    qatomic_set(&test_ns,41*sec); CHECK(Query(&f,bytes,sizeof(bytes),0)==0);
    CHECK(le32toh(rows[z].flags)==3);
    qatomic_set(&test_ns,61*sec); CHECK(Query(&f,bytes,sizeof(bytes),0)==0);
    CHECK(le32toh(rows[z].flags)==7);
    // 部分读支持及错误偏移；停用后原累计路径仍运行，不生成新遥测。
    uint8_t slice[8]; CHECK(Query(&f,slice,8,56+z*88+8)==0);
    uint64_t partial; memcpy(&partial,slice,sizeof(partial));
    CHECK(le64toh(partial)==1);
    CHECK(Query(&f,slice,8,3)==(NVME_INVALID_FIELD|NVME_DNR));
    CHECK(Query(&f,slice,8,56+f.ns.num_zones*88)==(NVME_INVALID_FIELD|NVME_DNR));
    f.s.rr_history_enabled=false;
    CHECK(Query(&f,bytes,sizeof(bytes),0)==(NVME_INVALID_LOG_ID|NVME_DNR));
    Read(&f,0,0,0,1);
    CHECK(f.s.ch[0].fc[0].plane[0].blk[1].read_count==1);
    CHECK(f.blocks[z][0].count==0);
    f.s.rr_threshold=0; Read(&f,0,0,0,1);
    CHECK(f.s.ch[0].fc[0].plane[0].blk[1].read_count==1);
    Destroy(&f);
  }
  printf("FEMU生产读取/C2h/Reset内存联调：%u项检查通过（测试替身）\n",checks);
}
