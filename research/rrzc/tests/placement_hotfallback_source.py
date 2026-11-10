"""基于原G2生成独立的H普通迁移目标改进，不叠加W迁移禁止规则。"""
import difflib
import json
from pathlib import Path
import shutil
from calibration_source import once
from placement_wmig_source import BASE,digest,prepare as prepare_previous

MODE='G2_HFALLBACK'
ROOT=Path(__file__).resolve().parents[1]


def mode_header(text):
    text=once(text,'kG2WriteMigrationOff };','kG2WriteMigrationOff, kG2HotFallback };')
    text=once(text,'    case Mode::kG2WriteMigrationOff:',
              '    case Mode::kG2HotFallback: return "G2_HFALLBACK";\n    case Mode::kG2WriteMigrationOff:')
    return once(text,'  else throw std::invalid_argument("放置模式必须明确为G0、G1、G2或G2_WMIG_OFF");',
                '  else if (!std::strcmp(value,"G2_HFALLBACK")) mode=Mode::kG2HotFallback;\n'
                '  else throw std::invalid_argument("放置模式必须明确为G0、G1、G2、G2_WMIG_OFF或G2_HFALLBACK");')


def zbd(text):
    text=once(text,'#include "rrzc_placement_trace.h"',
              '#include "rrzc_hot_fallback.h"\n#include "rrzc_placement_trace.h"')
    text=once(text,'std::string *observation,uint8_t content) {',
              'std::string *observation,uint8_t content,bool migration) {')
    anchor='  uint64_t observed = 0, busy = 0, empty = 0, full = 0, partial = 0;'
    text=once(text,anchor,'''  const bool improved=migration && rrzc_hot_fallback::Enabled(placement_mode_);
  auto* batch=improved?rrzc_hot_fallback::current:nullptr;
  if(batch && batch->Device()!=CompositionDevice())batch=nullptr;
  const auto tag=static_cast<uint8_t>(rrzc_colocation::current.tag);
  rrzc_hot_fallback::Rank selected_rank{};
'''+anchor)
    anchor='        if (diff <= best_diff) {'
    text=once(text,anchor,'''        const bool source=improved && (rrzc_colocation::current.sources.count(z->start_) ||
            (batch && batch->Source(z->start_)));
        const bool avoid=batch && batch->Avoid(z->start_,z->GetResetState().generation);
        const auto rank=rrzc_hot_fallback::Ranking(tag,avoid,z->used_capacity_.load(),diff,z->start_);
        const bool better=improved ? (!source && diff<=LIFETIME_DIFF_NOT_GOOD &&
            (!allocated_zone || rank<selected_rank)) : diff<=best_diff;
        if (better) {''')
    text=once(text,'          best_diff = diff;','          best_diff = diff;\n          if(improved)selected_rank=rank;')
    text=once(text,'  *best_diff_out = best_diff;', '''  if(improved && allocated_zone)
    Info(logger_,"H回退目标排序：对象=%lu，标签=%u，目标=%lu，代际=%lu，已有有效=%lu，剩余=%lu，本批H目标=%d，按有效量排序=%d，资源额外申请=0",
        rrzc_colocation::current.owner,static_cast<unsigned>(tag),allocated_zone->start_,
        allocated_zone->GetResetState().generation,allocated_zone->used_capacity_.load(),allocated_zone->capacity_,
        batch && batch->Avoid(allocated_zone->start_,allocated_zone->GetResetState().generation),bool(tag&1));
  *best_diff_out = best_diff;''')
    text=once(text,'out_zone, min_capacity,nullptr,rrzc_content::kSst);',
              'out_zone, min_capacity,nullptr,rrzc_content::kSst,true);')
    anchor='    Info(logger_, "TakeMigrateZone: %lu", (*out_zone)->start_);'
    text=once(text,anchor,'''    if(rrzc_hot_fallback::Enabled(placement_mode_) &&
        (static_cast<uint8_t>(rrzc_colocation::current.tag)&1)) {
      auto* batch=rrzc_hot_fallback::current;
      if(batch && batch->Device()==CompositionDevice())
        batch->Hot((*out_zone)->start_,(*out_zone)->GetResetState().generation);
    }
'''+anchor)
    return text


def fs(text):
    text=once(text,'#include "rrzc_placement_trace.h"',
              '#include "rrzc_hot_fallback.h"\n#include "rrzc_placement_trace.h"')
    text=once(text,'  rrzc_phase_time::Scope phase_migrate(rrzc_phase_time::kMigrateExtents);',
              '  rrzc_phase_time::Scope phase_migrate(rrzc_phase_time::kMigrateExtents);\n'
              '  rrzc_hot_fallback::Batch placement_batch(\n'
              '      rrzc_hot_fallback::Enabled(rrzc_placement::ProcessSettings().mode),zbd_->CompositionDevice());')
    return once(text,'  for (const auto& it : file_extents) {',
                '  if(rrzc_hot_fallback::Enabled(rrzc_placement::ProcessSettings().mode))\n'
                '    placement_batch.Sources(source_zones);\n  for (const auto& it : file_extents) {')


def prepare(output):
    output=Path(output)
    prepare_previous(output)
    source=output/'源码';directory=source/'协同池副本/plugin/zenfs/fs'
    for name,transform in [('rrzc_placement_mode.h',mode_header),('zbd_zenfs.cc',zbd),('fs_zenfs.cc',fs)]:
        path=directory/name;path.write_text(transform(path.read_text()))
    path=directory/'zbd_zenfs.h'
    path.write_text(once(path.read_text(),'std::string *observation = nullptr,\n                                uint8_t content=rrzc_content::kOther);',
                         'std::string *observation = nullptr,\n                                uint8_t content=rrzc_content::kOther,bool migration=false);'))
    adapter=directory/'rrzc_colocation_zenfs.inc'
    adapted=once(adapter.read_text(),
        '         placement_mode_==rrzc_placement::Mode::kG2WriteMigrationOff);',
        '         placement_mode_==rrzc_placement::Mode::kG2WriteMigrationOff ||\n'
        '         placement_mode_==rrzc_placement::Mode::kG2HotFallback);')
    adapted=once(adapted,'  if(plan.action==rrzc_colocation::Action::kFallback) {',
        '''  if(plan.action==rrzc_colocation::Action::kFallback) {
    // 两臂共同记录必要H回退的原观测，不新增设备查询，不参与目标决策。
    if(!own_open && (static_cast<uint8_t>(rrzc_colocation::current.tag)&1)) {
      Info(logger_,"H协同回退资源：对象=%lu，需要=%u，空闲Active=%u，空闲Open=%u，原因=%s",
          rrzc_colocation::current.owner,required,resources.free_active,resources.free_open,plan.reason.c_str());
      for(const auto& member:coloc_pool_->Snapshot())for(const auto& row:observed)
        if(row.id==member.zone)
          Info(logger_,"H协同回退成员：对象=%lu，Zone=%lu，代际=%lu，状态=%u，剩余=%lu，忙=%d，安全=%d，内容合格=%d，源排除=%d",
              rrzc_colocation::current.owner,row.id,row.generation,static_cast<unsigned>(row.state),row.remaining,
              row.busy,row.risk_safe,row.content_compatible,rrzc_colocation::current.sources.count(row.id)!=0);
    }''')
    adapter.write_text(adapted)
    shutil.copyfile(ROOT/'model/rrzc_hot_fallback.h',directory/'rrzc_hot_fallback.h')
    shutil.copyfile(directory/'rrzc_placement_mode.h',source/'rrzc_placement_mode.h')
    script=source/'run_placement_device_guest.py'
    script.write_text(once(script.read_text(),"'G2_WMIG_OFF')","'G2_WMIG_OFF','G2_HFALLBACK')"))
    identity=json.loads((source/'二阶副本身份.json').read_text())
    identity['新源码']={p.name:digest(p) for p in directory.iterdir() if p.is_file()}
    identity['H普通回退']=dict(模式=MODE,叠加W禁止=False,增加池名额=False,组成账目不参与选择=True,
                                 策略='仅重排原合格普通部分区；本批其他迁移软避开H目标')
    identity['第六阶段']['模式']=MODE
    identity['第六阶段']['门控']='不启用W迁移禁止；仅改进H普通回退目标'
    (source/'二阶副本身份.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    manifest={str(p.relative_to(source)):digest(p) for p in source.rglob('*') if p.is_file() and p.name!='传输身份.json'}
    (source/'传输身份.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    old=BASE/'源码/协同池副本/plugin/zenfs/fs';patch=[]
    for p in sorted(directory.iterdir()):
        if not p.is_file():continue
        before=(old/p.name).read_text() if (old/p.name).exists() else ''
        after=p.read_text()
        if before!=after:patch.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                         fromfile='冻结G2/'+p.name,tofile='H普通回退/'+p.name))
    (output/'H普通回退差异.patch').write_text(''.join(patch))
    (output/'副本身份.json').write_text(json.dumps(dict(来源=str(BASE),模式=MODE,
        新源码=identity['新源码'],原G2保留=True,GC及RR触发与选区函数正文不变=True,
        修改边界='fs仅增加本批作用域；zbd仅增加迁移目标排序与本批登记；不改变复制映射和资源路径'),
        ensure_ascii=False,indent=2)+'\n')
    return identity
