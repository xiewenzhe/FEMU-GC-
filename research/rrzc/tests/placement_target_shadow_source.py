"""在已验收影子版的独立副本增加同次普通备选观察，原策略正文保留。"""
import difflib
import json
from pathlib import Path
import shutil

from calibration_source import once
from placement_wmig_source import digest

ROOT=Path(__file__).resolve().parents[1]
BASE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-placement-wmig-ws-sb1-20261109-results')


def adapter(text):
    text=once(text,'  std::vector<rrzc_colocation::Zone> result;','''  // 原扫描已取得的Zone状态复制给独立观察，不反向写回row或候选。
  if(auto* shadow=rrzc_target_shadow::current)shadow->Begin([&] {
    return coloc_pool_?coloc_pool_->Snapshot():std::vector<rrzc_colocation::Member>{};
  });
  std::vector<rrzc_colocation::Zone> result;''')
    text=once(text,'if(!z->Acquire()) {row.busy=true;result.push_back(row);continue;}',
        '''if(!z->Acquire()) {
      if(auto* shadow=rrzc_target_shadow::current)shadow->Unknown(row.id);
      row.busy=true;result.push_back(row);continue;
    }''')
    text=once(text,'    const auto released=z->CheckRelease();\n    if(!released.ok())throw std::runtime_error(released.ToString());\n    result.push_back(row);',
        '''    if(auto* shadow=rrzc_target_shadow::current)shadow->Capture([&] {
      rrzc_target_shadow::Row observed;
      observed.zone=row.id;observed.generation=row.generation;
      observed.known=generation.known && generation.stable;
      observed.remaining=z->capacity_;observed.used=z->used_capacity_.load();
      observed.full=z->IsFull();observed.pool=shadow->Member(row.id);
      observed.compatible=!IsContentGuardEnabled() ||
          rrzc_content::Compatible(z->rrzc_content_.load(),rrzc_content::kSst);
      observed.difference=GetLifeTimeDiff(z->lifetime_,
          static_cast<Env::WriteLifeTimeHint>(shadow->Lifetime()));
      const auto count=counts.find(row.id);
      observed.rc_known=threshold && count!=counts.end();
      observed.rc=observed.rc_known?count->second:0;observed.threshold=threshold;
      return observed;
    });
    const auto released=z->CheckRelease();
    if(!released.ok())throw std::runtime_error(released.ToString());
    result.push_back(row);''')
    text=once(text,'  *output=nullptr;','''  *output=nullptr;
  rrzc_target_shadow::Scope target_shadow(
      (placement_mode_==rrzc_placement::Mode::kG2 || rrzc_write_survivor::Enabled(placement_mode_)) &&
      rrzc_target_shadow::Enabled(),static_cast<unsigned>(lifetime),own_open?0:required);''')
    text=once(text,'                      const rrzc_colocation::Resources& budget) {',
        '''                      const rrzc_colocation::Resources& budget) {
    if(target_shadow.Active()) {
      try {
        const auto detail=target_shadow.Describe(rrzc_colocation::current.owner,
            static_cast<uint8_t>(rrzc_colocation::current.tag),own_open,
            static_cast<unsigned>(choice.action),choice.zone,choice.generation,
            LIFETIME_DIFF_NOT_GOOD,budget.free_active,budget.free_open);
        Info(logger_,"目标备选影子：%s",detail.c_str());
      }catch(...) {
        target_shadow.Invalidate();
        Info(logger_,"目标备选影子未知：对象=%lu，原因=观测格式化失败，原分配继续=1",
             rrzc_colocation::current.owner);
      }
    }''')
    return text


def prepare(output):
    output=Path(output)
    if output.exists():raise ValueError('不覆盖已有独立副本')
    source=BASE/'源码'
    manifest=json.loads((source/'传输身份.json').read_text())
    if not all(digest(source/name)==expected for name,expected in manifest.items()):
        raise ValueError('原验收源码身份变化')
    output.mkdir(parents=True)
    new=output/'源码';shutil.copytree(source,new)
    fs=new/'协同池副本/plugin/zenfs/fs'
    target=fs/'rrzc_colocation_zenfs.inc';target.write_text(adapter(target.read_text()))
    target=fs/'zbd_zenfs.cc';target.write_text('#include "rrzc_target_shadow.h"\n'+target.read_text())
    shutil.copyfile(ROOT/'model/rrzc_target_shadow.h',fs/'rrzc_target_shadow.h')
    device=new/'run_placement_device_guest.py'
    device.write_text(once(device.read_text(),"ZENFS_RRZC_V1_SHADOW='1')",
        "ZENFS_RRZC_V1_SHADOW='1',ZENFS_RRZC_TARGET_SHADOW='1')"))
    identity=json.loads((new/'二阶副本身份.json').read_text())
    identity['新源码']={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    identity['普通备选观测']=dict(来源=str(BASE),默认关闭=True,
        开关='ZENFS_RRZC_TARGET_SHADOW=1',策略不变=True,
        限制='复用逐区原观察，不是原子快照；预留/替补后续未知；建议不保证最终分配成功')
    (new/'二阶副本身份.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    manifest={str(p.relative_to(new)):digest(p) for p in new.rglob('*')
              if p.is_file() and p.name!='传输身份.json'}
    (new/'传输身份.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    patch=[]
    for name in ('rrzc_colocation_zenfs.inc','zbd_zenfs.cc','rrzc_target_shadow.h'):
        old=source/'协同池副本/plugin/zenfs/fs'/name
        before=old.read_text() if old.exists() else ''
        patch.extend(difflib.unified_diff(before.splitlines(True),(fs/name).read_text().splitlines(True),
            fromfile='已验收影子版/'+name,tofile='同次普通备选观测/'+name))
    patch.extend(difflib.unified_diff((source/'run_placement_device_guest.py').read_text().splitlines(True),
        device.read_text().splitlines(True),fromfile='原设备入口/run_placement_device_guest.py',
        tofile='观测开启设备入口/run_placement_device_guest.py'))
    (output/'代码差异.patch').write_text(''.join(patch))
    proof=dict(来源=str(BASE),源码SHA256=manifest,仅观察=True,未执行完整构建=True,未运行业务=True)
    (output/'副本身份.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    print(json.dumps(prepare(parser.parse_args().output),ensure_ascii=False,indent=2))
