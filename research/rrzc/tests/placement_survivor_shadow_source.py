"""仅在已验收W历史准入副本增加只读旧G2建议；不改变准入或分配算法。"""
import difflib
import json
from pathlib import Path
import shutil

from calibration_source import once
from placement_wmig_source import digest

BASE = Path('/home/data/xwz/rrzc-baselines/joint-controlled-placement-wmig-ws-b2-20261109-results')
MODE = 'G2_WSURVIVOR'


def adapter(text):
    begin = text.index('  auto observed=ColocationObservation(true);', text.index('IOStatus ZonedBlockDevice::TryColocation'))
    end = text.index('  if(plan.action==rrzc_colocation::Action::kFallback)', begin)
    original_observation = text[begin:end]
    anchor = '  // 只限制已有成功映射证据的W-only；'
    observer = '''  // 同一调用的旧G2只读建议；Choose不Commit，不申请令牌，不复制。
  // 可知仅表示原建议可重建，不保证后续Acquire/资源复核/Commit一定成功。
  auto log_shadow=[&](const rrzc_colocation::Decision& choice,
                      const rrzc_colocation::Resources& budget) {
    if(!own_open && static_cast<uint8_t>(rrzc_colocation::current.tag)==2 &&
       (placement_mode_==rrzc_placement::Mode::kG2 || rrzc_write_survivor::Enabled(placement_mode_)))
      Info(logger_,"W旧策略影子：对象=%lu，历史=%u，需要=%u，可知=1，建议动作=%u，建议Zone=%lu，代际=%lu，修订=%lu，空闲Active=%u，空闲Open=%u，原因=%s，未申请令牌=1，未提交=1",
           rrzc_colocation::current.owner,static_cast<unsigned>(rrzc_colocation::current.write_history),
           required,static_cast<unsigned>(choice.action),choice.zone,choice.generation,choice.revision,
           budget.free_active,budget.free_open,choice.reason.c_str());
  };
''' + anchor
    text = once(text, anchor, observer)
    gated = '    Info(logger_,"W历史准入回退：模式=G2_WSURVIVOR，'
    at = text.index(gated)
    # 拒绝路径原先没有原生观察；新增观察仅供影子，失败必须继续原拒绝/安全回退。
    observation = '''    try {
      if(coloc_pool_) {
''' + original_observation + '''        log_shadow(plan,resources);
      } else {
        Info(logger_,"W旧策略影子未知：对象=%lu，原因=池不存在，原门控继续=1",rrzc_colocation::current.owner);
      }
    } catch(const std::exception& error) {
      Info(logger_,"W旧策略影子未知：对象=%lu，原因=%s，原门控继续=1",rrzc_colocation::current.owner,error.what());
    }
'''
    text = text[:at] + observation + text[at:]
    # 非拒绝路径直接复用原本的Choose结果，避免第二次查询或多次扫描。
    text = once(text, '  if(plan.action==rrzc_colocation::Action::kFallback)',
                '  log_shadow(plan,resources);\n  if(plan.action==rrzc_colocation::Action::kFallback)')
    return text


def prepare(output):
    output = Path(output)
    assert not output.exists(), '不覆盖旧副本'
    source = BASE / '源码'
    original_manifest = json.loads((source / '传输身份.json').read_text())
    assert all(digest(source/name)==sha for name,sha in original_manifest.items()), '验收源码身份改变'
    output.mkdir(parents=True)
    new = output / '源码';shutil.copytree(source, new)
    fs = new / '协同池副本/plugin/zenfs/fs'
    path = fs / 'rrzc_colocation_zenfs.inc'
    path.write_text(adapter(path.read_text()))
    path = fs / 'fs_zenfs.cc'
    path.write_text(once(path.read_text(),
        '    if(rrzc_write_survivor::Enabled(rrzc_placement::ProcessSettings().mode)) {',
        '    if(rrzc_write_survivor::Enabled(rrzc_placement::ProcessSettings().mode) ||\n'
        '       rrzc_placement::ProcessSettings().mode==rrzc_placement::Mode::kG2) {'))
    identity = json.loads((new / '二阶副本身份.json').read_text())
    identity['新源码'] = {p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    identity['影子归因'] = dict(来源=str(BASE),模式=['G2',MODE],两个模式都追踪成功历史=True,
        原G2实际策略不变=True,新准入规则不变=True,影子不Commit=True,
        观察代价='拒绝路径多一次原状态采集，含短暂Acquire/Release与RC查询；会影响时序，不是零开销')
    (new / '二阶副本身份.json').write_text(json.dumps(identity,ensure_ascii=False,indent=2)+'\n')
    manifest = {str(p.relative_to(new)):digest(p) for p in new.rglob('*') if p.is_file() and p.name!='传输身份.json'}
    (new / '传输身份.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    differences = []
    for name in ['rrzc_colocation_zenfs.inc','fs_zenfs.cc']:
        old = source / '协同池副本/plugin/zenfs/fs' / name
        differences.extend(difflib.unified_diff(old.read_text().splitlines(True),(fs/name).read_text().splitlines(True),
            fromfile='已验收W历史版/'+name,tofile='旧G2影子观测/'+name))
    (output / '代码差异.patch').write_text(''.join(differences))
    proof = dict(来源=str(BASE),修改文件=['rrzc_colocation_zenfs.inc','fs_zenfs.cc'],
        新源码=identity['新源码'],算法未调整=True,未运行完整业务=True,
        影子边界='Choose建议可行不等于最终分配成功；未知影子仍按原历史门控回退')
    (output / '副本身份.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof
