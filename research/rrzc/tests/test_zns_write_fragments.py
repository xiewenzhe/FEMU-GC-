"""抽取实际zns_write循环，重放512B命令分包并核对逻辑页登记和物理映射。"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]


def function(source,name):
    a=source.index('static uint64_t '+name+'(')
    return source[a:source.index('\n}',a)+2]


HARNESS=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#define LOGICAL_PAGE_SIZE 4096
#define SRAM_WRITE_LATENCY_NS 1000
#define NVME_ZA_ZRWA_VALID 8
#define USER_IO 0
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define ftl_debug(...) ((void)0)
struct wc {uint64_t sblk,used,cap,lpns[64];};
struct zns_ssd {uint64_t lbasz;struct {int num_wc;struct wc write_cache[2];} cache;
uint64_t issued[8192],mapped[8192],next,entries;};
typedef struct {unsigned num_conv_zones;int zone_size_log2;uint64_t zone_size;
struct {struct {unsigned za;} d;} zone_array[2];} NvmeNamespace;
typedef struct {uint64_t slba,stime;uint32_t nlb;NvmeNamespace *ns;} NvmeRequest;
static int zns_get_wcidx(struct zns_ssd *z,uint64_t zone) {
 for(int i=0;i<z->cache.num_wc;i++)if(z->cache.write_cache[i].sblk==zone)return i;
 return -1;
}
/* 替身只分配递增PPA；被测逻辑为下方从仓库抽出的真实登记/淘汰循环。 */
static uint64_t zns_wc_flush(struct zns_ssd *z,int index,int type,uint64_t time) {
 (void)type;(void)time;struct wc *w=&z->cache.write_cache[index];
 for(uint64_t i=0;i<w->used;i++) {
  uint64_t lpn=w->lpns[i];z->issued[z->entries++]=lpn;z->mapped[lpn]=++z->next;
 }
 w->used=0;return 1;
}
/*ACTUAL*/
static void init(struct zns_ssd *z) {
 memset(z,0,sizeof(*z));z->lbasz=512;z->cache.num_wc=2;
 for(int i=0;i<2;i++){z->cache.write_cache[i].sblk=UINT64_MAX;z->cache.write_cache[i].cap=64;}
}
static void write_part(struct zns_ssd *z,NvmeNamespace *ns,uint64_t lba,uint32_t nlb) {
 NvmeRequest r={.ns=ns,.slba=lba,.nlb=nlb};zns_write(z,&r);
}
static void drain(struct zns_ssd *z) {
 for(int i=0;i<2;i++)zns_wc_flush(z,i,USER_IO,0);
}
static int same(struct zns_ssd *a,struct zns_ssd *b) {
 return a->entries==b->entries && !memcmp(a->issued,b->issued,a->entries*8)
  && !memcmp(a->mapped,b->mapped,sizeof(a->mapped));
}
int main(void) {
 NvmeNamespace ns={.zone_size_log2=16,.zone_size=65536};struct zns_ssd a,b;
 init(&a);init(&b);
 /* 实测分包形状：完整2048扇区和2023+25扇区覆盖相同字节。 */
 write_part(&a,&ns,0,76);write_part(&b,&ns,0,76);
 write_part(&a,&ns,76,2048);write_part(&b,&ns,76,2023);write_part(&b,&ns,2099,25);
 drain(&a);drain(&b);if(!same(&a,&b))return 20;
 /* 全部512B扇区独立写入，跨页/跨缓存边界也应与整段相同。 */
 init(&a);init(&b);write_part(&a,&ns,0,4096);
 for(unsigned i=0;i<4096;i++)write_part(&b,&ns,i,1);
 drain(&a);drain(&b);if(!same(&a,&b))return 21;
 /* 换区写入不能使前区剩余页片段重新登记或产生额外淘汰。 */
 init(&a);write_part(&a,&ns,0,1);write_part(&a,&ns,65536,8);
 for(unsigned i=1;i<8;i++)write_part(&a,&ns,i,1);
 drain(&a);assert(a.entries==2);
 /* Conventional和ZRWA仍走原随机写登记，不应用顺序去重。 */
 for(unsigned i=0;i<2;i++) {
  init(&a);ns.num_conv_zones=i==0;ns.zone_array[0].d.za=i==1?8:0;
  write_part(&a,&ns,1,1);write_part(&a,&ns,1,1);drain(&a);assert(a.entries==2);
 }
 puts("实测分包、逐扇区、跨缓存、跨区及随机写保留验证通过");return 0;
}
'''


class WriteFragmentsTest(unittest.TestCase):
    def test_real_write_loop_is_partition_invariant_and_old_loop_fails(self):
        source=(ROOT/'hw/femu/zns/zftl.c').read_text()
        actual=function(source,'zns_write')
        old=actual
        a=old.index('    int wcidx;\n');b=old.index('    if(wcidx==-1)',a)
        old=old[:a]+'    int wcidx = zns_get_wcidx(zns, req_zone);\n\n'+old[b:]
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for tag,body in [('原始',old),('修复',actual)]:
                client=root/(tag+'.c');binary=root/tag
                client.write_text(HARNESS.replace('/*ACTUAL*/',body))
                subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-sign-compare',
                    '-fsanitize=undefined',str(client),'-o',str(binary)],check=True)
                reply=subprocess.run([str(binary)],capture_output=True,text=True)
                self.assertEqual(reply.returncode,20 if tag=='原始' else 0,reply.stderr)


if __name__=='__main__':unittest.main()
