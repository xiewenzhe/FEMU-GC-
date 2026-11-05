#!/usr/bin/env python3
"""生成独立GC教学实验的只读观测补丁，并核验真实迁移事件。"""
import difflib
import json
from pathlib import Path
import re


HELPER = r'''
// 教学实验只读观测；设备报告与主机有效量依次取得，不冒充跨层原子快照。
static bool GcPathTraceEnabled() {
  const char* value=getenv("ZENFS_GC_PATH_TRACE");
  return value && !strcmp(value,"1");
}

static void LogGcPath(const std::shared_ptr<Logger>& logger,
                      ZonedBlockDevice* device,const char* stage,uint64_t round) {
  if (!GcPathTraceEnabled()) return;
  const uint64_t begin=std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  // Linux块层报告的地址单位为512字节扇区；不采用NVMe设备LBA单位猜测。
  std::string path=device->GetFilename();
  if (path.empty() || path[0]!='/') path="/dev/"+path;
  const int fd=open(path.c_str(),O_RDONLY|O_CLOEXEC);
  const size_t count=device->GetNrZones();
  std::vector<uint64_t> storage((sizeof(struct blk_zone_report)+
      count*sizeof(struct blk_zone)+sizeof(uint64_t)-1)/sizeof(uint64_t));
  auto* report=reinterpret_cast<struct blk_zone_report*>(storage.data());
  report->sector=0;report->nr_zones=count;
  const bool good=fd>=0 && ioctl(fd,BLKREPORTZONE,report)==0 && report->nr_zones==count;
  const int saved_errno=errno;
  if (fd>=0) close(fd);
  std::vector<ZoneSnapshot> host;
  device->GetZoneSnapshot(host);
  uint64_t opened=0,active=0;
  if (good) for (unsigned i=0;i<report->nr_zones;++i) {
    const unsigned state=report->zones[i].cond;
    const bool is_open=state==BLK_ZONE_COND_IMP_OPEN || state==BLK_ZONE_COND_EXP_OPEN;
    opened+=is_open;active+=is_open || state==BLK_ZONE_COND_CLOSED;
  }
  const uint64_t end=std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  Info(logger,"GC路径资源：阶段=%s，轮次=%lu，报告成功=%d，错误码=%d，设备Open=%lu，设备Active=%lu，开始纳秒=%lu，结束纳秒=%lu，跨层原子=0",
       stage,round,good,good?0:saved_errno,opened,active,begin,end);
  device->LogZoneStats();
  if (!good) return;
  for (unsigned i=0;i<report->nr_zones;++i) {
    const auto& z=report->zones[i];
    const uint64_t start=z.start*512,wp=z.wp*512,capacity=z.capacity*512;
    const auto found=std::find_if(host.begin(),host.end(),
        [&](const ZoneSnapshot& x){return x.start==start;});
    const bool known=found!=host.end();
    const uint64_t valid=known?found->used_capacity:0;
    // Full可能含Finish放弃尾部，容量减有效量仅称回收余量，不称全为已写无效数据。
    const uint64_t written=z.cond==BLK_ZONE_COND_FULL?capacity:
        wp>=start && wp-start<=capacity?wp-start:0;
    Info(logger,"GC路径Zone：阶段=%s，轮次=%lu，Zone=%lu，状态码=%u，设备WP=%lu，容量=%lu，主机有效量已知=%d，有效字节=%lu，已用或封闭字节=%lu，回收余量字节=%lu，设备可追加字节=%lu",
         stage,round,start,static_cast<unsigned>(z.cond),wp,capacity,known,valid,written,
         known && written>=valid?written-valid:0,
         (z.cond==BLK_ZONE_COND_EMPTY || z.cond==BLK_ZONE_COND_IMP_OPEN ||
          z.cond==BLK_ZONE_COND_EXP_OPEN || z.cond==BLK_ZONE_COND_CLOSED) &&
          wp>=start && wp-start<=capacity?capacity-(wp-start):0);
  }
}
'''


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('GC路径补丁父源不唯一：'+before[:100])
    return text.replace(before, after, 1)


def patched(text):
    text = once(text, '#include <unistd.h>',
                '#include <unistd.h>\n#include <linux/blkzoned.h>\n#include <sys/ioctl.h>')
    marker='namespace ROCKSDB_NAMESPACE {\n\n// 只检查本进程 Reset'
    text = once(text, marker, 'namespace ROCKSDB_NAMESPACE {\n'+HELPER+'\n// 只检查本进程 Reset')
    text = once(text, '      ++zc_rounds_;\n      s = MigrateExtents(migrate_exts, &stats);',
        '      ++zc_rounds_;\n'
        '      LogGcPath(logger_,zbd_,"迁移前",zc_rounds_);\n'
        '      if (GcPathTraceEnabled()) for (const auto start:migrate_zones_start)\n'
        '        Info(logger_,"GC路径选中：轮次=%lu，Zone=%lu",zc_rounds_,start);\n'
        '      s = MigrateExtents(migrate_exts, &stats);\n'
        '      LogGcPath(logger_,zbd_,"迁移后",zc_rounds_);')
    marker = '    ext->start_ = target_start;\n    ext->zone_ = target_zone;'
    text = once(text, marker,
        '    if (GcPathTraceEnabled())\n'
        '      Info(logger_,"GC路径复制：文件编号=%lu，文件名=%s，源Zone=%lu，源片段=%lu，目标Zone=%lu，目标片段=%lu，有效字节=%lu，累计迁移统计字节=%lu，复制成功=1，映射提交=尚未确认",\n'
        '           zfile->GetID(),fname.c_str(),observed_source,observed_source_start,\n'
        '           target_zone->start_,target_start,ext->length_,stats->migrated_bytes);\n'+marker)
    marker = '    IOStatus sync_status = SyncFileExtents(zfile.get(), new_extent_list);'
    text = once(text, marker, marker+'\n'
        '    if (GcPathTraceEnabled())\n'
        '      Info(logger_,"GC路径映射提交：文件编号=%lu，文件名=%s，成功=%d，状态=%s",\n'
        '           zfile->GetID(),fname.c_str(),sync_status.ok(),sync_status.ToString().c_str());')
    return text


def make_patch(text):
    return ''.join(difflib.unified_diff(text.splitlines(True),patched(text).splitlines(True),
        fromfile='a/fs/fs_zenfs.cc',tofile='b/fs/fs_zenfs.cc'))


def fields(line, marker):
    return dict(part.split('=',1) for part in line.split(marker,1)[1].strip().split('，'))


def analyze(root):
    """只接受复制、映射成功、源Reset和后置设备状态彼此一致的真实闭环。"""
    root=Path(root);result=[];rr=0
    for path in sorted(root.glob('*.zenfs.log')):
        rounds={};current=None
        for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
            if 'RR 统计：' in line: rr+=1
            if 'GC路径资源：' in line:
                item=fields(line,'GC路径资源：');key=int(item['轮次'])
                if item['阶段']=='迁移前':
                    current=dict(日志=path.name,轮次=key,起始行=number,资源前=item,
                        选中=[],复制=[],提交=[],重置=[],区前={},区后={})
                    rounds[key]=current
                else:
                    if key not in rounds:raise ValueError('后置资源没有对应起点')
                    rounds[key]['资源后']=item
            elif 'GC路径Zone：' in line:
                item=fields(line,'GC路径Zone：');key=int(item['轮次'])
                rounds[key]['区前' if item['阶段']=='迁移前' else '区后'][item['Zone']]=item
            elif current is not None:
                for marker,name in (('GC路径选中：','选中'),('GC路径复制：','复制'),
                    ('GC路径映射提交：','提交'),('Zone 重置诊断：','重置')):
                    if marker in line:current[name].append(dict(行=number,**fields(line,marker)))
                match=re.search(r'ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone',line)
                if match:
                    if int(match[1])!=current['轮次']:raise ValueError('轮次错配')
                    current.update(迁移统计字节=int(match[2]),统计重置区数=int(match[3]),结束行=number)
                    result.append(current);current=None
        if current is not None:raise ValueError('存在没有完成统计的GC')
    if rr:raise ValueError('隔离GC实验出现RR维护')
    checked=[]
    for frame in result:
        if frame['资源前']['报告成功']!='1' or frame['资源后']['报告成功']!='1':
            raise ValueError('设备状态报告失败')
        copies=frame['复制'];commits=frame['提交']
        if not copies or any(c['成功']!='1' for c in commits):raise ValueError('没有成功复制或映射提交失败')
        for copy in copies:
            if not any(c['文件编号']==copy['文件编号'] and c['行']>copy['行'] and c['成功']=='1' for c in commits):
                raise ValueError('复制没有后续成功映射提交')
        for selected in frame['选中']:
            zone=selected['Zone'];before=frame['区前'][zone];after=frame['区后'][zone]
            resets=[r for r in frame['重置'] if r['Zone']==zone and r['状态']=='成功']
            if not resets or after['状态码']!='1' or after['设备WP']!=zone or after['有效字节']!='0':
                raise ValueError('选中源区没有成功Reset或迁移后不是空区')
            for copy in (c for c in copies if c['源Zone']==zone):
                submitted=[c['行'] for c in commits if c['文件编号']==copy['文件编号'] and
                    c['行']>copy['行'] and c['成功']=='1']
                if not any(r['行']>min(submitted) for r in resets):
                    raise ValueError('没有证据证明源Reset发生在对应映射提交之后')
            checked.append(dict(日志=frame['日志'],轮次=frame['轮次'],源Zone字节地址=int(zone),
                源Zone编号=int(zone)//(128<<20),有效字节=int(before['有效字节']),
                回收余量字节=int(before['回收余量字节']),
                复制有效字节=sum(int(c['有效字节']) for c in copies if c['源Zone']==zone),
                目标Zone编号=sorted({int(c['目标Zone'])//(128<<20) for c in copies if c['源Zone']==zone}),
                设备资源前={k:int(frame['资源前'][k]) for k in ('设备Open','设备Active')},
                设备资源后={k:int(frame['资源后'][k]) for k in ('设备Open','设备Active')},
                源Reset成功=True,源Reset行=[r['行'] for r in resets],
                起始行=frame['起始行'],结束行=frame['结束行']))
    return dict(已观察GC轮数=len(result),已闭环源Zone数=len(checked),
        不同源Zone数=len({r['源Zone编号'] for r in checked}),RR维护次数=rr,
        迁移统计字节=sum(f['迁移统计字节'] for f in result),逐区=checked,逐轮原始= result,
        边界='设备资源为报告时实测；有效量随后读取，存在并发变化；回收余量可能含Finish尾部；不是性能对照')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('结果目录');args=parser.parse_args()
    print(json.dumps(analyze(args.结果目录),ensure_ascii=False,indent=2))
