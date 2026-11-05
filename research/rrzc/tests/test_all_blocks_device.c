/* 提取真实C4h函数；时钟、DMA和NAND对象为替身，不是设备实验。 */
#include <assert.h>
#include <endian.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../../hw/femu/zns/rr-history.h"
#define QEMU_PACKED __attribute__((packed))
#define NVME_INVALID_LOG_ID 9
#define NVME_INVALID_FIELD 2
#define NVME_DNR 0x4000
#define QEMU_CLOCK_REALTIME 0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define cpu_to_le32 htole32
#define cpu_to_le64 htole64
#define le32_to_cpu le32toh
#define le64_to_cpu le64toh
#define qatomic_read(p) (*(p))
#define qemu_mutex_lock pthread_mutex_lock
#define qemu_mutex_unlock pthread_mutex_unlock
#define g_malloc0(n) calloc(1,(n))
#define g_free free
#include "wire.inc"
typedef struct {pthread_mutex_t lock;uint64_t generation,born_ns;FemuRrHistory* blocks;} ZnsRrZoneHistory;
struct zns_ssd {
  uint32_t num_ch,chnls_per_zone,num_lun,num_plane;
  bool rr_history_enabled;uint64_t rr_threshold,rr_session_hi,rr_session_lo;
  ZnsRrZoneHistory* rr_history;
  struct {struct {struct {struct {uint64_t read_count;} blk[2];} plane[2];} fc[2];} ch[4];
};
typedef struct {struct zns_ssd* zns;uint32_t num_zones;struct {struct {uint64_t zslba;} d;} zone_array[8];} NvmeNamespace;
typedef struct {unsigned unused;} FemuCtrl;
typedef struct {struct {uint64_t prp1,prp2;} dptr;uint32_t cdw11;} NvmeCmd;
static uint64_t now;
static uint64_t qemu_clock_get_ns(int ignored){(void)ignored;return now;}
static uint16_t dma_read_prp(FemuCtrl* n,void* data,size_t len,uint64_t p1,uint64_t p2) {
  (void)n;(void)p2;memcpy((void*)(uintptr_t)p1,data,len);return 0;
}
#include "device.inc"
int main(void) {
  for(uint32_t cpz=1;cpz<=4;cpz*=2) {
    struct zns_ssd s={0};s.num_ch=4;s.chnls_per_zone=cpz;s.num_lun=s.num_plane=2;
    s.rr_history_enabled=true;s.rr_threshold=1024;s.rr_session_hi=7;
    ZnsRrZoneHistory histories[8]={0};FemuRrHistory history[8][16]={0};s.rr_history=histories;
    NvmeNamespace ns={.zns=&s,.num_zones=8/cpz};
    const uint32_t z=4/cpz,count=cpz*4;
    for(unsigned i=0;i<ns.num_zones;++i) {
      pthread_mutex_init(&histories[i].lock,NULL);histories[i].blocks=history[i];
      ns.zone_array[i].d.zslba=i*262144;
    }
    s.ch[0].fc[0].plane[0].blk[1].read_count=980;
    s.ch[cpz-1].fc[1].plane[1].blk[1].read_count=500;
    for(unsigned i=0;i<20;++i)femu_rr_history_record(&history[z][count-1],35*FEMU_RR_SECOND_NS,1024);
    now=40*FEMU_RR_SECOND_NS;
    uint8_t bytes[56+16*88];FemuCtrl ctrl={0};
    NvmeCmd cmd={.dptr={.prp1=(uintptr_t)bytes},.cdw11=htole32(z<<16)};
    assert(zns_rr_blocks_log(&ctrl,&ns,&cmd,sizeof(bytes),0)==0);
    ZnsRrRecentEntry* rows=(void*)(bytes+56);
    assert(le64toh(rows[0].current_rc)==980 && le64toh(rows[count-1].current_rc)==500);
    assert(le64toh(rows[count-1].delta10)==20 && le64toh(rows[count-1].delta30)==20);
    for(unsigned i=0;i<count;++i) {
      assert(le32toh(rows[i].block_id)==i && le32toh(rows[i].zone_index)==z);
      assert(le64toh(rows[i].snapshot_ns)==now && le32toh(rows[i].block)==1);
    }
    assert(le32toh(rows[count-1].ch)==cpz-1 && le32toh(rows[count-1].plane)==1);
    assert(zns_rr_blocks_log(&ctrl,&ns,&cmd,4,3)==(NVME_INVALID_FIELD|NVME_DNR));
    assert(zns_rr_blocks_log(&ctrl,&ns,&cmd,4,56+count*88)==(NVME_INVALID_FIELD|NVME_DNR));
    cmd.cdw11=htole32(ns.num_zones<<16);
    assert(zns_rr_blocks_log(&ctrl,&ns,&cmd,sizeof(bytes),0)==(NVME_INVALID_FIELD|NVME_DNR));
    s.rr_history_enabled=false;
    assert(zns_rr_blocks_log(&ctrl,&ns,&cmd,sizeof(bytes),0)==(NVME_INVALID_LOG_ID|NVME_DNR));
    for(unsigned i=0;i<ns.num_zones;++i) {
      for(unsigned b=0;b<count;++b)femu_rr_history_destroy(&history[i][b]);
      pthread_mutex_destroy(&histories[i].lock);
    }
  }
  puts("真实C4h生产函数：跨通道分组、全部块身份、同帧读取增量和错误偏移通过");
}
