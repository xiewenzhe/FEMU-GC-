"""在真实Flush来源副本接在线读热识别；保留历史元数据角色用于恢复池预算。"""
from calibration_source import once


def zbd_header(text):
    return once(text,'#pragma once','#pragma once\n#include "rrzc_stream_profile.h"')


def io_header(text):
    text=once(text,'#include "zbd_zenfs.h"','#include "zbd_zenfs.h"\n#include "rrzc_stream_profile.h"')
    text=once(text,'  uint8_t coloc_tag_=0;','  rrzc_stream::Profile stream_profile_;\n  std::atomic<bool> stream_sst_{false};\n  uint8_t coloc_tag_=0;')
    return once(text,'  uint8_t ColocationTag() {return coloc_tag_?coloc_tag_:zbd_->ColocationTag(GetFilename());}',
        '''  uint8_t ColocationTag() {
    if(rrzc_stream::Enabled())return (flush_origin_w_?2:0) | (stream_profile_.Snapshot(file_size_).hot?1:0);
    return coloc_tag_?coloc_tag_:zbd_->ColocationTag(GetFilename());
  }
  uint8_t ColocationRestoreTag() {return rrzc_stream::Enabled()?coloc_tag_:ColocationTag();}''')


def io(text):
    # H统计为当前进程的逻辑文件历史，数据搬迁不清零；重挂载重新学习。
    text=once(text,'  coloc_tag_=ColocationTag();\n    PutFixed32(output,kColocationMembership);',
        '''  if(rrzc_stream::Enabled()) {
      const auto current=ColocationTag();
      if(flush_origin_w_)coloc_tag_=2;
      else if(current)coloc_tag_=current;
      else if(!coloc_tag_ && !membership.empty())coloc_tag_=1;
    }else coloc_tag_=ColocationTag();
    PutFixed32(output,kColocationMembership);''')
    text=once(text,'        linkfiles_.push_back(slice.ToString());',
        '''        linkfiles_.push_back(slice.ToString());
        if(slice.size()>=4 && slice.ToString().substr(slice.size()-4)==".sst")stream_sst_=true;''')
    text=once(text,'  for (const auto& name : update->GetLinkFiles()) linkfiles_.push_back(name);',
        '''  for (const auto& name : update->GetLinkFiles()) linkfiles_.push_back(name);
  stream_sst_=stream_sst_.load() || update->stream_sst_.load();''')
    text=once(text,'  linkfiles_.push_back(linkf);',
        '  linkfiles_.push_back(linkf);\n  if(linkf.size()>=4 && linkf.substr(linkf.size()-4)==".sst")stream_sst_=true;')
    text=once(text,'    if (r <= 0) break;', '''    if (r <= 0) break;
    if(rrzc_stream::Enabled() && stream_sst_.load()) {
      const auto profile=stream_profile_.Read(offset+read,static_cast<uint64_t>(r),file_size_);
      if(profile.changed)std::fprintf(stderr,"在线读热变化：文件编号=%lu，H=%d，窗口读取=%lu，重复读取=%lu，读取字节=%lu，事件=%lu，统计非RC=1\\n",
          file_id_,profile.hot,profile.reads,profile.reused,profile.bytes,rrzc_stream::Now());
    }''')
    # 全部三种原生写路径成功后才推进SST事件钟；RR复制不走这些文件Append入口。
    for call in ('s = active_zone_->Append(buffer, wr_size + pad_sz);',
                 's = active_zone_->Append(sparse_buffer, wr_size + pad_sz);',
                 's = active_zone_->Append((char*)data + offset, wr_size);'):
        anchor='    '+call+'\n    if (!s.ok()) return s;'
        text=once(text,anchor,anchor+'\n    if(rrzc_stream::Enabled() && stream_sst_.load())rrzc_stream::Tick();')
    return once(text,'if(flush_origin_w_ || !zbd_->ColocationPool() || !rrzc_flush_origin::current.active || ColocationTag())return;',
        'if(flush_origin_w_ || (!zbd_->ColocationPool() && !rrzc_stream::Enabled()) || !rrzc_flush_origin::current.active || ColocationTag())return;')


def fs(text):
    # 恢复的是池历史成员资格，不把重挂载前的H判断冒充当前热度。
    marker='roles[ext->zone_->start_]|=file->ColocationTag();'
    text=once(text,marker,'roles[ext->zone_->start_]|=file->ColocationRestoreTag();')
    return once(text,'roles[z->start_]|=file->ColocationTag();','roles[z->start_]|=file->ColocationRestoreTag();')


def adapter(text):
    text=once(text,'// 放在独立zbd翻译单元的rocksdb命名空间中；仅显式K=1时启用。',
              '// 在线分类仅用于独立机制副本，原型仍固定K=1。')
    return once(text,'  const char* path=getenv("ZENFS_RRZC_POOL_TAGS");','''  if(rrzc_stream::Enabled()) {
    if(max_nr_active_io_zones_<2)throw std::invalid_argument("协同池缺少普通Active预算");
    coloc_pool_=std::make_unique<rrzc_colocation::Pool>(1);
    Info(logger_,"在线协同池：K=1，H来源=实际SST重复读取，W来源=真实Flush代理，无手工文件清单=1");
    return;
  }
  const char* path=getenv("ZENFS_RRZC_POOL_TAGS");''')
