"""仅在独立对象中接入净复制/回收评分，默认保留原时序模型。"""
from calibration_source import once


def direct(source,reclaim_price=False):
    text=once(source,'  decision={};\n  if(deadline_enabled)',
        '  const bool unit_cost=getenv("ZENFS_RRZC_UNIT_COST") &&\n'
        '      !strcmp(getenv("ZENFS_RRZC_UNIT_COST"),"1");\n'
        '  decision={};\n  if(deadline_enabled)')
    anchor='    const auto plan=rrzc_deadline::Select(inputs,selection_need);'
    replacement='''    // 新模型不构造多区未来计划；旧路径在开关关闭时原样执行。
    const auto plan=unit_cost ? rrzc_deadline::Decision{} :
        rrzc_deadline::Select(inputs,selection_need);
    if(unit_cost) {
      const auto chosen=rrzc_unit_cost::Select(inputs);
      for(const auto& r:chosen.rows)
        Info(logger_,"单位回收成本：轮次=%lu，Zone=%lu，有效=%lu，回收=%lu，RR抵扣=%lu，净复制=%lu",
            shadow_round,r.zone,r.valid,r.reclaimed,r.credit,r.net);
      if(chosen.found)for(const auto& row:ranked)if(row.candidate.zone==chosen.zone) {
        decision.found=true;decision.selected=row.candidate;break;
      }
      Info(logger_,"单位回收建议：轮次=%lu，存在=%d，Zone=%lu，安全候选=%zu，补充区计划=0",
          shadow_round,chosen.found,chosen.found?chosen.zone:0,chosen.rows.size());
    }'''
    text=once(text,anchor,replacement)
    if reclaim_price:
        old='      const auto chosen=rrzc_unit_cost::Select(inputs);'
        text=once(text,old,'''      std::vector<rrzc_reclaim_price::Evidence> space_evidence;
      const auto chosen=rrzc_reclaim_price::Select(inputs,need,&space_evidence);
      for(const auto& e:space_evidence)
        Info(logger_,"RR空间替代成本：轮次=%lu，Zone=%lu，原RR抵扣=%lu，预计RR空间=%lu，替代空间=%lu，空间价格复制=%lu，空间价格回收=%lu，空间扣款=%lu，有效RR抵扣=%lu，全额空间=%d",
            shadow_round,e.zone,e.raw_credit,e.rr_space,e.replaced_space,e.price_copy,e.price_space,
            e.debit,e.effective_credit,e.full_space);''')
    return text


def frontend(source,reclaim_price=False):
    name='rrzc_reclaim_price_selector.h' if reclaim_price else 'rrzc_unit_cost_selector.h'
    return '#include "'+name+'"\n'+source


def wrapper(source):
    text=once(source,'RRZC_CAL_COMMON_OBSERVATION=1',
        'ZENFS_RRZC_UNIT_COST=1 RRZC_CAL_COMMON_OBSERVATION=1')
    text=once(text,'"$target/g1" matched','"${initial_reference}" matched')
    return once(text,'result=$target/$arm_tag',
        'result=$target/$arm_tag\ninitial_reference=$target/g1\n'
        'if [[ $arm_tag == p1 || $arm_tag == p2 ]]; then initial_reference=$target/p1; fi')
