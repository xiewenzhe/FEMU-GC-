"""在冻结ZenFS独立副本生成默认关闭的采样范围/代表块诊断补丁，不改选择器。"""
import difflib
from pathlib import Path
from calibration_source import once

DIAG=r'''
        if(all_blocks_diagnostic) {
          // 每个候选只记录当前最大RC块，不能把最大值换块当作同块增长。
          const rrzc_all_blocks::Evidence::BlockEvidence* representative=nullptr;
          for(const auto& item:proof.distribution)
            if(!representative || item.block.rc>representative->block.rc ||
                (item.block.rc==representative->block.rc && item.block.block_id<representative->block.block_id))
              representative=&item;
          if(representative) {
            const auto& x=representative->block;const auto& g=representative->growth;
            Info(logger_,"全块代表门控：轮次=%lu，Zone=%lu，代际=%lu，块=%u，PPA=%u/%u/%u/%u，快照=%lu，RC=%lu，十秒增量=%lu，三十秒增量=%lu，标志=%u，有前帧=%d，前快照=%lu，前代际=%lu，前块=%u，前RC=%lu，近期可知=%d，近期增量=%lu，间隔=%lu，风险可知=%d，ETA=%lu，增长原因=%s",
                shadow_round,c.zone,x.generation,x.block_id,x.ch,x.lun,x.plane,x.block,
                x.snapshot_ns,x.rc,x.delta10,x.delta30,x.flags,g.has_previous,g.previous.snapshot_ns,
                g.previous.generation,g.previous.block_id,g.previous.rc,g.known,g.delta,g.interval_ns,
                representative->cost.risk_known,representative->cost.eta,g.reason);
          } else {
            Info(logger_,"全块代表缺帧：轮次=%lu，Zone=%lu，可用C2=%d，可用C4=%d，历史存在=%d",
                shadow_round,c.zone,b!=usable.end(),found!=all_blocks_current.end(),history!=all_blocks_frozen.end());
          }
        }
'''


def patched(fs,inc):
    if 'ZENFS_RRZC_SAMPLE_PARTIAL' in fs or '全块代表门控：' in inc:
        raise ValueError('采样修正已存在，禁止重复插入')
    original=fs;anchor='  rrzc_all_blocks::Store all_blocks_history;'
    fs=once(fs,anchor,r'''  const char* partial_env=getenv("ZENFS_RRZC_SAMPLE_PARTIAL");
  const char* diagnostic_env=getenv("ZENFS_RRZC_BLOCK_DIAG");
  if((partial_env && strcmp(partial_env,"0") && strcmp(partial_env,"1")) ||
      (diagnostic_env && strcmp(diagnostic_env,"0") && strcmp(diagnostic_env,"1"))) {
    Error(logger_,"全块采样开关非法，仅接受0或1");return;
  }
  const bool sample_partial=partial_env && !strcmp(partial_env,"1");
  const bool all_blocks_diagnostic=diagnostic_env && !strcmp(diagnostic_env,"1");
  if((sample_partial || all_blocks_diagnostic) && !all_blocks_enabled) {
    Error(logger_,"全块采样扩展要求原全块风险入口已启用");return;
  }
  Info(logger_,"全块采样范围：未满区=%d，代表诊断=%d，默认值均为0，候选满区资格不变=1",
      sample_partial,all_blocks_diagnostic);
'''+anchor)
    fs=once(fs,'      if(!zone || !zone->IsFull())continue;',
        '      if(!zone || (!sample_partial && !zone->IsFull()))continue;')
    marker='        Info(logger_,"全块风险代表：轮次=%lu'
    inc=once(inc,marker,DIAG+'\n'+marker)
    anchor='    uint64_t non_free = zbd_->GetUsedSpace()'
    if original.split(anchor,1)[1]!=fs.split(anchor,1)[1]:raise ValueError('原GC触发/候选正文被修改')
    return fs,inc


def make_patch(fs,inc):
    changed=patched(fs,inc)
    return ''.join(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),
        fromfile='a/fs/'+name,tofile='b/fs/'+name))
        for name,a,b in zip(('fs_zenfs.cc','rrzc_direct_zenfs.inc'),(fs,inc),changed))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();text=make_patch((args.source/'fs_zenfs.cc').read_text(),(args.source/'rrzc_direct_zenfs.inc').read_text())
    with args.output.open('x') as stream:stream.write(text)
