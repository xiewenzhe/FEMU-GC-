"""配对准入边界与实际冻结源码接线核验；不将其称为设备功能验收。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import pair_admission_source as patch

ROOT=Path(__file__).resolve().parents[1]
FROZEN=Path('/home/data/xwz/rrzc-baselines/joint-controlled-sst-flush-w-v3-20261030-results')
FS=FROZEN/FROZEN.name.removesuffix('-results')/'协同池副本/plugin/zenfs/fs'


class PairTest(unittest.TestCase):
    def test_real_recover_bodies_publish_after_extent_insertion(self):
        text=patch.io((FS/'io_zenfs.cc').read_text())
        def body(marker):
            start=text.index(marker);opening=text.index('{',start);depth=1;end=opening+1
            while depth:
                depth+=(text[end]=='{')-(text[end]=='}');end+=1
            return text[start:end]
        functions='\n'.join(body(marker) for marker in (
            'void ZoneFile::PublishPairExtents()', 'void ZoneFile::ClearExtents()',
            'IOStatus ZoneFile::RecoverSparseExtents(', 'IOStatus ZoneFile::Recover()'))
        # 使用真实转换后函数体，后端只提供受控字节；不声称模拟设备崩溃恢复。
        harness=r'''
#include "rrzc_pair_admission.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
struct IOStatus {
  bool success=true;
  static IOStatus OK(){return {};}
  static IOStatus IOError(const char*){return {false};}
  bool ok() const {return success;}
};
struct Zone {uint64_t start_=0,used_capacity_=0,wp_=0;};
struct ZoneExtent {
  uint64_t start_,length_;Zone* zone_;
  ZoneExtent(uint64_t start,uint64_t length,Zone* zone):start_(start),length_(length),zone_(zone){}
};
struct Device {
  Zone zone;rrzc_pair::Ledger ledger;
  bool PairAdmissionObserved(){return true;}
  rrzc_pair::Ledger& PairLedger(){return ledger;}
  Zone* GetIOZone(uint64_t){return &zone;}
  int Read(char* data,uint64_t,uint32_t n,bool){std::memset(data,0,n);data[0]=char(128);return n;}
};
static uint64_t DecodeFixed64(const char* data){return static_cast<unsigned char>(data[0]);}
struct ZoneFile {
  static constexpr uint64_t NO_EXTENT=UINT64_MAX,SPARSE_HEADER_SIZE=8;
  Device* zbd_;uint8_t coloc_tag_=2;
  std::vector<std::string> linkfiles_{"/example.sst"};
  std::vector<ZoneExtent*> extents_;
  uint64_t extent_start_=0,file_size_=0;bool is_sparse_=false;
  uint8_t ColocationTag(){return coloc_tag_;}
  bool HasActiveExtent(){return extent_start_!=NO_EXTENT;}
  uint32_t GetBlockSize(){return 4096;}
  void PublishPairExtents();void ClearExtents();
  IOStatus RecoverSparseExtents(uint64_t,uint64_t,Zone*);IOStatus Recover();
};
'''+functions+r'''
int main(){
  for(bool sparse:{false,true}){
    Device device;device.zone.wp_=sparse?8192:4096;
    ZoneFile file;file.zbd_=&device;file.is_sparse_=sparse;
    assert(file.Recover().ok());
    const uint64_t expected=sparse?256:4096;
    assert(file.file_size_==expected && device.zone.used_capacity_==expected);
    auto row=device.ledger.Read(0);assert(row.total==expected && row.w==expected);
    file.ClearExtents();assert(device.zone.used_capacity_==0 && device.ledger.Read(0).total==0);
  }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'recover.cc';source.write_text(harness)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(source),'-o',str(root/'recover')],check=True)
            subprocess.run([str(root/'recover')],check=True)

    def test_policy_windows_live_mapping_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'policy'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_pair_admission.cc'),'-o',str(out)],check=True)
            subprocess.run([str(out)],check=True)

    def test_frozen_source_preserves_gc_rr_and_metadata(self):
        for n,fn in (('zbd_zenfs.h',patch.zbd_header),('io_zenfs.h',patch.io_header),
                     ('io_zenfs.cc',patch.io),('fs_zenfs.cc',patch.fs),
                     ('rrzc_colocation_zenfs.inc',patch.adapter)):
            old=(FS/n).read_text();new=fn(old)
            with self.assertRaises(ValueError):fn(new)
            if n=='fs_zenfs.cc':
                # GCWorker和完整RRWorker逐字不变。
                before=old.split('IOStatus ZenFS::SyncFileExtents(',1)[0]
                after=new.split('IOStatus ZenFS::SyncFileExtents(',1)[0]
                self.assertEqual(before,after)
            if n=='io_zenfs.cc':
                encoding=lambda s:s.split('void ZoneFile::EncodeTo',1)[1].split('Status ZoneFile::MergeUpdate',1)[0]
                self.assertEqual(encoding(old).replace('  MetadataSynced();','  MetadataSynced();\n  PublishPairExtents();'),encoding(new))
                for marker in ('zone->used_capacity_ += extent_length;', 'zone->used_capacity_ += to_recover;'):
                    tail=new.split(marker,1)[1]
                    self.assertLess(tail.index('extents_.push_back'),tail.index('PublishPairExtents();'))

    def test_business_only_switch_and_same_observation(self):
        old=(FROZEN/'源码/run_pool_business_guest.sh').read_text();new=patch.business_wrapper(old)
        subprocess.run(['bash','-n'],input=new,text=True,check=True)
        self.assertIn('ZENFS_RRZC_POOL_PAIR_OBSERVE=1',new)
        self.assertIn('pair_on="${RRZC_PAIR_ARM:-0}";flush_w=1;hotspot=1;phase run',new)
        self.assertIn('pair_on=0;flush_w=0;hotspot=0;phase verify',new)
        for token in ('ZENFS_RRZC_CANDIDATE_POOL=original','--reads=393216 --writes=737280'):
            self.assertIn(token,new)

    def test_arm_wrapper_preserves_greedy_argument_and_arm(self):
        for value in (0,1):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);wrapper=root/'arm.sh';wrapper.write_text(patch.arm_wrapper(value))
                (root/'run_pool_business_guest.sh').write_text(
                    '#!/bin/bash\nset -eu\n[[ $# == 1 && $1 == greedy ]]\nprintf "%s" "$RRZC_PAIR_ARM"\n')
                out=subprocess.check_output(['bash',str(wrapper),'greedy'],text=True)
                self.assertEqual(out,str(value))


if __name__=='__main__':unittest.main()
