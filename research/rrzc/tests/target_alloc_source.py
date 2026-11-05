"""只给独立ZenFS对象添加目标扫描采证，不增加Acquire或改变筛选规则。"""
from calibration_source import once


def patched(source):
    begin='IOStatus ZonedBlockDevice::GetBestOpenZoneMatch('
    end='IOStatus ZonedBlockDevice::AllocateEmptyZone('
    assert source.count(begin)==source.count(end)==1,'目标函数边界不唯一'
    prefix,body=source.split(begin,1);body,suffix=body.split(end,1)
    text=once(body,'  std::ostringstream partial_details;\n',
        '  std::ostringstream partial_details;\n'
        '  const bool target_trace=observation && *observation=="RRZC_TARGET_DIAG";\n'
        '  std::ostringstream target_details;\n')
    text=once(text,'    if (z->Acquire()) {\n      // 仅在原分配扫描已经取得独占权时读取诊断字段，不新增 Zone 锁操作。',
        '    if (z->Acquire()) {\n'
        '      if (target_trace) target_details << " [Zone=" << z->start_\n'
        '        << "，取得=1，WP=" << z->wp_ << "，剩余=" << z->capacity_\n'
        '        << "，有效=" << z->used_capacity_.load() << "，满=" << z->IsFull()\n'
        '        << "，类别=" << unsigned(z->rrzc_content_.load())\n'
        '        << "，寿命=" << int(z->lifetime_)\n'
        '        << "，匹配差=" << GetLifeTimeDiff(z->lifetime_,file_lifetime) << "]";\n'
        '      // 仅在原分配扫描已经取得独占权时读取诊断字段，不新增 Zone 锁操作。')
    text=once(text,'    } else if (observation != nullptr) {\n      ++busy;\n',
        '    } else if (observation != nullptr) {\n'
        '      if (target_trace) target_details << " [Zone=" << z->start_ << "，取得=0]";\n'
        '      ++busy;\n')
    text=once(text,'  *best_diff_out = best_diff;\n  *zone_out = allocated_zone;',
        '  if (target_trace) Info(logger_,"迁移目标扫描：最小容量=%u，文件寿命=%d，匹配Zone=%lu，最佳差=%u，候选=%s",\n'
        '    min_capacity,int(file_lifetime),allocated_zone?allocated_zone->start_:UINT64_MAX,\n'
        '    best_diff,target_details.str().c_str());\n'
        '  *best_diff_out = best_diff;\n  *zone_out = allocated_zone;')
    text=prefix+begin+text+end+suffix
    old='''  unsigned int best_diff = LIFETIME_DIFF_NOT_GOOD;
  auto s =
      GetBestOpenZoneMatch(file_lifetime, &best_diff, out_zone, min_capacity,nullptr,rrzc_content::kSst);'''
    new='''  unsigned int best_diff = LIFETIME_DIFF_NOT_GOOD;
  const char* target_flag=getenv("RRZC_CAL_TARGET_TRACE");
  const bool trace_target=target_flag && !strcmp(target_flag,"1");
  std::string target_observation=trace_target?"RRZC_TARGET_DIAG":"";
  auto s =
      GetBestOpenZoneMatch(file_lifetime, &best_diff, out_zone, min_capacity,
          trace_target?&target_observation:nullptr,rrzc_content::kSst);'''
    text=once(text,old,new)
    return text


def controlled_retry(source):
    """仅显式批次受控模式：分配重试不得穿插进一轮尚未结束的GC维护。"""
    text=once(source,'          for (unsigned int attempt = 0; attempt < attempts; ++attempt) {',
        '          for (unsigned int attempt = 0; attempt < attempts; ++attempt) {\n'
        '            // 受控模式只串行单次重试与维护；睡眠前释放，避免阻止GC腾空间。\n'
        '            std::unique_lock<std::recursive_mutex> boundary_retry;\n'
        '            const char* boundary_path=getenv("ZENFS_RRZC_CAL_GC_GATE");\n'
        '            if (boundary_path && boundary_path[0]) boundary_retry=StrictLock();')
    text=once(text,'            if (attempt + 1 < attempts) usleep(50 * 1000);',
        '            if (boundary_retry.owns_lock()) boundary_retry.unlock();\n'
        '            if (attempt + 1 < attempts) usleep(50 * 1000);')
    return text


def synchronous_space_retry(source):
    """先完整尝试原分配，失败才请求维护；不把短暂fallback登记当成必须GC。"""
    text=once(source,'#include "zbd_zenfs.h"','#include "zbd_zenfs.h"\n#include "rrzc_boundary_sync.h"')
    text=once(text,'          for (unsigned int attempt = 0; attempt < attempts; ++attempt) {',
        '          const auto boundary_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(60);\n'
        '          for (unsigned int attempt = 0; attempt < attempts; ++attempt) {')
    old='''            if (boundary_retry.owns_lock()) boundary_retry.unlock();
            if (attempt + 1 < attempts) usleep(50 * 1000);'''
    new='''            if (boundary_retry.owns_lock()) boundary_retry.unlock();
            if (boundary_path && boundary_path[0] && attempt+1<attempts) {
              const uint64_t request=rrzc_boundary::State().Request();
              Info(logger_,"受控空间请求：序号=%lu，重试=%u，原分配重试未成功=1",request,attempt+1);
              if (!request || !rrzc_boundary::State().Wait(request,boundary_deadline)) {
                PutOpenIOZoneToken();
                return IOStatus::IOError("受控空间请求未在60秒内完成，停止而非绕过维护");
              }
              Info(logger_,"受控空间确认：序号=%lu，下一步重试原分配=1",request);
            } else if (attempt + 1 < attempts) usleep(50 * 1000);'''
    return once(text,old,new)
