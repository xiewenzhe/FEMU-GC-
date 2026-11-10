"""第六阶段只转换独立副本：W-only必要迁移准入和两臂共同aux调用计时。"""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
from calibration_source import once

BASE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-placement-exit-fix-v2-20261106-results')
MODE='G2_WMIG_OFF'
GATE='''  // 仅限制必要迁移中的W-only；不改真实标签，不获取令牌，不阻止必要迁移。
  if(rrzc_placement::SkipWriteMigration(placement_mode_,own_open,
         static_cast<uint8_t>(rrzc_colocation::current.tag))) {
    Info(logger_,"W迁移准入回退：模式=G2_WMIG_OFF，对象=%lu，类别=%u，需要字节=%u，路径=必要迁移，仅跳过协同目标=1，继续普通分配=1",
         rrzc_colocation::current.owner,static_cast<unsigned>(rrzc_colocation::current.tag),required);
    return IOStatus::OK();
  }
'''

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def mode_header(text):
    text=once(text,'kLegacy, kG0, kG1, kG2 };','kLegacy, kG0, kG1, kG2, kG2WriteMigrationOff };')
    text=once(text,'    case Mode::kG2: return "G2";',
        '    case Mode::kG2WriteMigrationOff: return "G2_WMIG_OFF";\n    case Mode::kG2: return "G2";')
    text=once(text,'  else throw std::invalid_argument("放置模式必须明确为G0、G1或G2");',
        '  else if (!std::strcmp(value,"G2_WMIG_OFF")) mode=Mode::kG2WriteMigrationOff;\n'
        '  else throw std::invalid_argument("放置模式必须明确为G0、G1、G2或G2_WMIG_OFF");')
    return once(text,'inline bool SkipHotMigration(',
        'inline bool SkipWriteMigration(Mode mode, bool new_file, uint8_t tag) {\n'
        '  return mode==Mode::kG2WriteMigrationOff && !new_file && tag==2;\n}\n\ninline bool SkipHotMigration(')

def adapter(text):
    text=once(text,'         placement_mode_==rrzc_placement::Mode::kG2);',
        '         placement_mode_==rrzc_placement::Mode::kG2 ||\n'
        '         placement_mode_==rrzc_placement::Mode::kG2WriteMigrationOff);')
    return once(text,'  if(!coloc_pool_ || rrzc_colocation::current.tag==rrzc_colocation::Tag::kNormal)',
        GATE+'  if(!coloc_pool_ || rrzc_colocation::current.tag==rrzc_colocation::Tag::kNormal)')

def aux_flush(text):
    # 内存计时置于前标记输出之后，析构置于后标记输出之前；真实调用和状态处理保持原样。
    text=once(text,'      s = output_file_directory_->FsyncWithDirOptions(',
        '      {\n      rrzc_phase_time::Scope aux_sync(rrzc_phase_time::kAuxDirectorySync,\n'
        '          static_cast<uint64_t>(job_context_->job_id));\n'
        '      s = output_file_directory_->FsyncWithDirOptions(')
    return once(text,'      trace_phase("目录同步后");','      }\n      trace_phase("目录同步后");')

def phase_header(text):
    return once(text,'kStrictRR = 11, kSpaceRecovery = 12, kRRCheck = 13',
        'kStrictRR = 11, kSpaceRecovery = 12, kRRCheck = 13,\n  kAuxDirectorySync = 14')

def device_probe(text):
    text='#include "rrzc_flush_origin.h"\n#include <type_traits>\n'+text
    text=once(text,'int Exercise(int argc,char** argv) {',
        '// 用公开方法的参数推导统计类型，不改生产头文件访问权限。\n'
        'template<class C,class R,class A,class B> B MigrationStatsParameter(R(C::*)(A,B));\n'
        'using ProbeMigrationStats=std::remove_pointer_t<decltype(MigrationStatsParameter(&ZenFS::MigrateExtents))>;\n'
        'int Exercise(int argc,char** argv) {')
    anchor='    std::cout<<"重挂载、完整内容和删除映射验证通过\\n";'
    extra=r'''    // 真实Flush来源上下文生成W-only载荷，不读取到H阈值，不主动构造RR风险。
    const std::string wname="/rr-path/flush-origin.sst";
    FileOptions wopt;wopt.use_direct_reads=wopt.use_direct_writes=true;
    std::unique_ptr<FSWritableFile> ww;
    {
      rrzc_flush_origin::Scope flush(true,900001);
      check(fs.NewWritableFile(wname,wopt,&ww,nullptr));
      ww->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
      check(ww->Append(Slice(expected.data,MiB),IOOptions(),nullptr));
      check(ww->Fsync(IOOptions(),nullptr));check(ww->Close(IOOptions(),nullptr));ww.reset();
    }
    ZenFSSnapshot before_w;fs.GetZenFSSnapshot(before_w,options);
    std::vector<ZoneExtentSnapshot*> parts;
    for(auto& ext:before_w.extents_)if(ext.filename==wname)parts.push_back(&ext);
    require(parts.size()==1 && parts[0]->length==MiB,"W载荷初始映射错误");
    const uint64_t wsource=parts[0]->zone_start;
    ProbeMigrationStats migrated;check(fs.MigrateExtents(parts,&migrated));
    require(migrated.migrated_bytes==MiB,"W必要迁移复制量不符");
    ZenFSSnapshot after_w;fs.GetZenFSSnapshot(after_w,options);
    uint64_t wtarget=UINT64_MAX;
    for(const auto& ext:after_w.extents_)if(ext.filename==wname)wtarget=ext.zone_start;
    require(wtarget!=UINT64_MAX && wsource!=wtarget,"W必要迁移未更新映射");
    std::unique_ptr<FSRandomAccessFile> wr;check(fs.NewRandomAccessFile(wname,wopt,&wr,nullptr));
    Slice actual;check(wr->Read(0,MiB,IOOptions(),&actual,scratch.data,nullptr));
    require(actual.size()==MiB && !std::memcmp(actual.data(),expected.data,MiB),"W必要迁移内容错误");wr.reset();
    std::cout<<"W-only必要迁移与内容验证通过，源="<<wsource<<"，目标="<<wtarget<<"\n";
'''
    return once(text,anchor,extra+'\n'+anchor)

def prepare(output):
    output=Path(output);assert not output.exists();output.mkdir(parents=True)
    original=BASE/'源码';identity=json.loads((original/'传输身份.json').read_text())
    assert all(digest(original/p)==h for p,h in identity.items()),'冻结构建来源发生变化'
    new=output/'源码';shutil.copytree(original,new)
    fs=new/'协同池副本/plugin/zenfs/fs';oldfs=original/'协同池副本/plugin/zenfs/fs'
    changes={}
    for name,transform in [('rrzc_placement_mode.h',mode_header),('rrzc_colocation_zenfs.inc',adapter),('rrzc_phase_time.h',phase_header)]:
        before=(fs/name).read_text();after=transform(before);(fs/name).write_text(after);changes[str((fs/name).relative_to(new))]=(before,after)
    f=new/'诊断-flush_job.cc';before=f.read_text();after=aux_flush(before)
    if '#include "rrzc_phase_time.h"' not in after:after='#include "rrzc_phase_time.h"\n'+after
    f.write_text(after);changes[f.name]=(before,after)
    # 客户端和Flush所在目录优先搜索同名头，所有同名副本必须同步。
    for name in ('rrzc_phase_time.h','rrzc_placement_mode.h','rrzc_stream_profile.h','rrzc_flush_origin.h'):
        if (fs/name).exists():shutil.copyfile(fs/name,new/name)
    ident=json.loads((new/'二阶副本身份.json').read_text());ident['新源码']={p.name:digest(p) for p in fs.iterdir() if p.is_file()}
    ident['第六阶段']=dict(基准=str(BASE),模式=MODE,门控='仅必要迁移tag==2',共享aux实际调用计时=True)
    (new/'二阶副本身份.json').write_text(json.dumps(ident,ensure_ascii=False,indent=2)+'\n')
    probe=new/'放置设备小测.cc';probe.write_text(device_probe(probe.read_text()))
    script=new/'run_placement_device_guest.py';text=script.read_text();text=once(text,"mode in ('G0','G1','G2')","mode in ('G0','G1','G2','G2_WMIG_OFF')");script.write_text(text)
    # manifest在全部修改后生成；旧manifest不覆盖任何冻结原始文件。
    manifest={str(p.relative_to(new)):digest(p) for p in new.rglob('*') if p.is_file() and p.name!='传输身份.json'}
    (new/'传输身份.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    patch=''.join(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='冻结G2/'+n,tofile='第六阶段/'+n)) for n,(a,b) in changes.items())
    (output/'代码差异.patch').write_text(patch)
    proof=dict(来源=str(BASE),原程序SHA256='db1c4489f9bd29eecf83d07f04cce0b94c30dc2af61b72f6b87f44759973088d',
        模式=['G2',MODE],新源码=ident['新源码'],修改文件=list(changes),
        原映射与维护源未改变=all((fs/n).read_bytes()==(oldfs/n).read_bytes() for n in ('fs_zenfs.cc','io_zenfs.cc','io_zenfs.h','zbd_zenfs.cc','zbd_zenfs.h','rrzc_colocation_pool.h','rrzc_stream_profile.h')))
    assert proof['原映射与维护源未改变']
    (output/'副本身份.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof
