"""仅向已验收副本插入区间计时；保留请求、选择和维护正文。"""
from calibration_source import once


def include(text):
    if '#include "rrzc_phase_time.h"' in text:
        raise ValueError('计时观测已接入，拒绝重复插入')
    return '#include "rrzc_phase_time.h"\n' + text


def client(text):
    start = text.index('  void RRZCSstLifetime(')
    stop = text.index('  void RRZCRotating(', start)
    prefix, suffix = text[:start], text[stop:]
    text = text[start:stop]
    text = once(text, '        auto status=db_.db->Flush(flush);',
        '        rrzc_phase_time::Scope phase_flush(rrzc_phase_time::kFlush,0,trace.Count());\n'
        '        auto status=db_.db->Flush(flush);')
    text = once(text, '      auto status=db_.db->WaitForCompact(WaitForCompactOptions());',
        '      rrzc_phase_time::Scope phase_compact(rrzc_phase_time::kCompactWait,0,trace.Count());\n'
        '      auto status=db_.db->WaitForCompact(WaitForCompactOptions());')
    text = once(text, '      rrzc_front_order::GcTick(getenv("RRZC_CAL_GC_GATE"),++stage);',
        '      { rrzc_phase_time::Scope phase_gc(rrzc_phase_time::kGcGate,0,trace.Count());\n'
        '        rrzc_front_order::GcTick(getenv("RRZC_CAL_GC_GATE"),++stage); }')
    text = once(text, '      rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0);',
        '      { rrzc_phase_time::Scope phase_layout(rrzc_phase_time::kLayout,0,trace.Count());\n'
        '        rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0); }')
    return include(prefix+text+suffix)


def zbd(text):
    for anchor, scope in (
        ('IOStatus Zone::Reset() {', 'kReset,start_'),
        ('IOStatus Zone::Finish() {', 'kFinish,start_'),
        ('void ZonedBlockDevice::WaitForOpenIOZoneToken(bool prioritized) {', 'kOpenToken'),
        ('                                           uint32_t min_capacity) {', 'kMigrateTarget'),
        ('                                          IOType io_type, Zone **out_zone,uint8_t content) {', 'kAllocate'),
    ):
        text = once(text, anchor, anchor+'\n  rrzc_phase_time::Scope phase_time(rrzc_phase_time::'+scope+');')
    text = once(text, '  std::unique_ptr<rrzc_space::Pressure::Wait> space_wait;',
        '  std::unique_ptr<rrzc_space::Pressure::Wait> space_wait;\n'
        '  std::unique_ptr<rrzc_phase_time::Scope> phase_space;')
    text = once(text, '            space_wait=std::make_unique<rrzc_space::Pressure::Wait>(allocation_pressure_);',
        '            phase_space=std::make_unique<rrzc_phase_time::Scope>(rrzc_phase_time::kSpaceRecovery);\n'
        '            space_wait=std::make_unique<rrzc_space::Pressure::Wait>(allocation_pressure_);')
    return include(text)


def io(text):
    text = once(text, '[&](bool* complete) {return zbd_->ServiceStrictRR(complete);}',
        '[&](bool* complete) {rrzc_phase_time::Scope phase_rr(rrzc_phase_time::kStrictRR);'
        'return zbd_->ServiceStrictRR(complete);}')
    return include(text)


def fs(text):
    text = once(text, 'IOStatus ZenFS::ReadReclaimOneZone() {',
        'IOStatus ZenFS::ReadReclaimOneZone() {\n'
        '  rrzc_phase_time::Scope phase_rr_check(rrzc_phase_time::kRRCheck);')
    # 此函数正文在不同副本中换行不同，先定位定义后的首个左花括号。
    start = text.index('IOStatus ZenFS::MigrateExtents(')
    pos = text.index('{', start)
    text = text[:pos+1]+'\n  rrzc_phase_time::Scope phase_migrate(rrzc_phase_time::kMigrateExtents);'+text[pos+1:]
    return include(text)


def wrapper(text):
    text = once(text, 'pool_k=1\nflush_w=0',
        'pool_k=0\nif [[ $arm_tag == m1 ]]; then pool_k=1; fi\nflush_w=0')
    return once(text, 'RRZC_CAL_TARGET_TRACE=1',
        'RRZC_PHASE_TIME="${request_trace:+$result/等待区间.bin}" RRZC_CAL_TARGET_TRACE=1')
