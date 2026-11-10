"""生成独立W历史准入副本；原G2、元数据格式和历史实验均保留。"""
import difflib
import json
from pathlib import Path
import shutil

from calibration_source import once
from placement_wmig_source import BASE, digest, prepare as common_prepare

MODE = 'G2_WSURVIVOR'
ROOT = Path(__file__).resolve().parents[1]


def device_probe(text):
    text = once(text, 'int main(int argc,char** argv) {', 'int PreviousMain(int argc,char** argv) {')
    return text + r'''

// 独立设备上的连续必要迁移；只调用原公开迁移接口，不伪造RR或受害区。
int HistoryExercise(int argc,char** argv) {
  try {
    require(argc==3,"历史设备测试参数错误");
    const std::string out=argv[1],name="/rr-path/history-extra.sst";
    Buffer expected,scratch;
    auto migrate=[&](ZenFS& fs,const char* phase) {
      ZenFSSnapshot snapshot;ZenFSSnapshotOptions options;options.zone_=options.zone_file_=true;
      fs.GetZenFSSnapshot(snapshot,options);std::vector<ZoneExtentSnapshot*> selected;
      for(auto& ext:snapshot.extents_)if(ext.filename==name)selected.push_back(&ext);
      require(selected.size()==1 && selected[0]->length==MiB,"历史测试源映射不符");
      const uint64_t source=selected[0]->zone_start,source_start=selected[0]->start;
      ProbeMigrationStats stats;check(fs.MigrateExtents(selected,&stats));
      require(stats.migrated_bytes==MiB,"历史测试复制量不符");
      ZenFSSnapshot after;fs.GetZenFSSnapshot(after,options);uint64_t target=UINT64_MAX,target_start=UINT64_MAX;
      for(const auto& ext:after.extents_)if(ext.filename==name){target=ext.zone_start;target_start=ext.start;}
      // 合成载荷的源区未写满，原普通路径可能在本区尾部追加；应核验真实物理extent变化。
      require(target!=UINT64_MAX && target_start!=source_start,"历史测试物理映射未更新");
      FileOptions opt;opt.use_direct_reads=true;std::unique_ptr<FSRandomAccessFile> reader;
      check(fs.NewRandomAccessFile(name,opt,&reader,nullptr));Slice actual;
      check(reader->Read(0,MiB,IOOptions(),&actual,scratch.data,nullptr));
      require(actual.size()==MiB && !std::memcmp(actual.data(),expected.data,MiB),"历史迁移完整内容错误");
      std::cout<<"W历史设备阶段："<<phase<<"，源="<<source<<"，源片段="<<source_start
        <<"，目标="<<target<<"，目标片段="<<target_start<<"，复制="<<MiB<<std::endl;
    };
    {
      std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/history.zenfs.log",&logger));
      logger->SetInfoLogLevel(DEBUG_LEVEL);
      auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
      check(owned->Open(false,true));ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
      FileOptions opt;opt.use_direct_writes=true;std::unique_ptr<FSWritableFile> writer;
      {
        rrzc_flush_origin::Scope flush(true,900002);
        check(fs.NewWritableFile(name,opt,&writer,nullptr));writer->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
        check(writer->Append(Slice(expected.data,MiB),IOOptions(),nullptr));
        check(writer->Fsync(IOOptions(),nullptr));check(writer->Close(IOOptions(),nullptr));writer.reset();
      }
      migrate(fs,"首次");migrate(fs,"本会话再次");
    }
    {
      std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/recovered.zenfs.log",&logger));
      logger->SetInfoLogLevel(DEBUG_LEVEL);
      auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
      check(owned->Open(false,true));ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
      migrate(fs,"重挂载未知历史");check(fs.DeleteFile(name,IOOptions(),nullptr));
      ZenFSSnapshot after;ZenFSSnapshotOptions options;options.zone_=options.zone_file_=true;
      fs.GetZenFSSnapshot(after,options);
      for(const auto& ext:after.extents_)require(ext.filename!=name,"历史测试删除后映射仍存在");
    }
    std::cout<<"W历史设备完整路径通过：三次必要迁移、重挂载和删除内容校验"<<std::endl;
    return 0;
  }catch(const std::exception& e){std::cerr<<"W历史设备测试失败："<<e.what()<<std::endl;return 1;}
}

int main(int argc,char** argv) {
  if(argc==3 && !std::strcmp(argv[2],"--history"))return HistoryExercise(argc,argv);
  return PreviousMain(argc,argv);
}
'''


def device_script(text):
    text = text.replace('G2_WMIG_OFF', MODE)
    anchor = "    (target/'设备功能完成.json').write_text("
    return once(text, anchor, '''    history=target/'历史准入功能';history.mkdir()
    active=dict(env,ZENFS_RRZC_PLACEMENT_TRACE=str(target/'历史组成.bin'))
    run([str(target/'placement_probe'),str(history),'--history'],'W历史连续迁移设备测试',active)
''' + anchor)


def mode_header(text):
    # 复用共同aux观测，但移除粗粒度W禁入模式，避免叠加旧实验规则。
    text = text.replace('kG2WriteMigrationOff', 'kG2WriteSurvivor')
    text = text.replace('G2_WMIG_OFF', MODE)
    begin = text.index('inline bool SkipWriteMigration(')
    end = text.index('inline bool SkipHotMigration(', begin)
    return text[:begin] + text[end:]


def adapter(text):
    text = '#include "rrzc_write_survivor.h"\n' + text
    text = text.replace('kG2WriteMigrationOff', 'kG2WriteSurvivor')
    start = text.index('  // 仅限制必要迁移中的W-only；')
    end = text.index('  if(!coloc_pool_', start)
    gate = '''  // 只限制已有成功映射证据的W-only；新Flush、首次迁移和HW仍沿原路径。
  if(rrzc_write_survivor::Skip(placement_mode_,own_open,
         static_cast<uint8_t>(rrzc_colocation::current.tag),
         rrzc_colocation::current.write_history)) {
    Info(logger_,"W历史准入回退：模式=G2_WSURVIVOR，对象=%lu，类别=%u，需要字节=%u，历史=本会话已提交迁移，继续普通分配=1",
         rrzc_colocation::current.owner,static_cast<unsigned>(rrzc_colocation::current.tag),required);
    return IOStatus::OK();
  }
'''
    text = text[:start] + gate + text[end:]
    anchor = '  // G1仅跳过必要迁移中的H/HW协同目标；'
    text = once(text, anchor, '''  if(!own_open && rrzc_write_survivor::Enabled(placement_mode_) &&
      static_cast<uint8_t>(rrzc_colocation::current.tag)==2)
    Info(logger_,"W历史准入评估：对象=%lu，历史状态=%u，所需字节=%u",
         rrzc_colocation::current.owner,static_cast<unsigned>(rrzc_colocation::current.write_history),required);
''' + anchor)
    return once(text, '  if(placement.Explicit())', '''  if(rrzc_write_survivor::Enabled(placement_mode_))
    Info(logger_,"W历史准入范围：仅本挂载会话，重挂载历史未知，未改变持久化格式，不叠加其他准入规则");
  if(placement.Explicit())''')


def scope(text):
    text = '#include "rrzc_write_survivor.h"\n' + text
    text = once(text, 'uint64_t owner=0;};',
        'uint64_t owner=0;rrzc_write_survivor::State write_history=rrzc_write_survivor::State::kNew;};')
    text = once(text, 'uint64_t owner=0):old_(current)',
        'uint64_t owner=0,rrzc_write_survivor::State history=rrzc_write_survivor::State::kNew):old_(current)')
    return once(text, 'std::move(sources),owner};', 'std::move(sources),owner,history};')


def io_header(text):
    text = '#include "rrzc_write_survivor.h"\n' + text
    text = once(text, '  bool flush_origin_w_=false;',
        '  rrzc_write_survivor::History write_survivor_;\n  bool flush_origin_w_=false;')
    return once(text, '  bool HasFlushOrigin() const', '''  rrzc_write_survivor::State WriteSurvivorState() const {return write_survivor_.Get();}
  void ObserveCommittedMigration(bool committed,bool live) {
    write_survivor_.AfterMapping(flush_origin_w_,committed,live);
  }
  bool HasFlushOrigin() const''')


def io_source(text):
    return once(text, 'Status ZoneFile::DecodeFrom(Slice* input) {',
        'Status ZoneFile::DecodeFrom(Slice* input) {\n'
        '  write_survivor_.Recovered();  // 旧格式无迁移历史；不能凭恢复推断文件从未迁移。')


def fs_source(text):
    text = once(text,
        'rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation,zfile->CompositionOwner());',
        'rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation,\n'
        '        zfile->CompositionOwner(),zfile->WriteSurvivorState());')
    return once(text, '    if (!sync_status.ok()) s = sync_status;', '''    // 只在真实映射提交成功后发布证据；失败复制、撤销及已删除对象不升级。
    // 同一次文件多extent迁移共用提交边界，不在循环中提前影响后续片段。
    if(rrzc_write_survivor::Enabled(rrzc_placement::ProcessSettings().mode)) {
      const bool live=sync_status.ok() && GetFile(fname).get()==zfile.get();
      zfile->ObserveCommittedMigration(sync_status.ok(),live);
      Info(logger_,"W历史映射结局：文件编号=%lu，对象=%lu，提交成功=%d，仍为当前文件=%d，历史状态=%u",
           zfile->GetID(),zfile->CompositionOwner(),sync_status.ok(),live,
           static_cast<unsigned>(zfile->WriteSurvivorState()));
    }
    if (!sync_status.ok()) s = sync_status;''')


def prepare(output):
    output = Path(output)
    common_prepare(output)
    source = output / '源码'
    fs = source / '协同池副本/plugin/zenfs/fs'
    for name, transform in [('rrzc_placement_mode.h', mode_header),
            ('rrzc_colocation_zenfs.inc', adapter), ('rrzc_colocation_scope.h', scope),
            ('io_zenfs.h', io_header), ('io_zenfs.cc', io_source), ('fs_zenfs.cc', fs_source)]:
        path = fs / name
        path.write_text(transform(path.read_text()))
    shutil.copyfile(ROOT / 'model/rrzc_write_survivor.h', fs / 'rrzc_write_survivor.h')
    shutil.copyfile(fs / 'rrzc_placement_mode.h', source / 'rrzc_placement_mode.h')
    shutil.copyfile(fs / 'rrzc_write_survivor.h', source / 'rrzc_write_survivor.h')
    device = source / 'run_placement_device_guest.py'
    device.write_text(device_script(device.read_text()))
    probe = source / '放置设备小测.cc'
    probe.write_text(device_probe(probe.read_text()))
    identity = json.loads((source / '二阶副本身份.json').read_text())
    identity['新源码'] = {p.name: digest(p) for p in fs.iterdir() if p.is_file()}
    identity['第六阶段'] = dict(基准=str(BASE), 模式=MODE, K=1,
        规则='本会话已有成功片段迁移提交的W-only在再次必要迁移时跳过池；H和HW不拒绝',
        历史持久化=False, 重挂载='未知；首次新的成功提交后才有证据',
        共享aux实际调用计时=True, 叠加WMIG禁止=False, 叠加H普通排序=False)
    (source / '二阶副本身份.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    manifest = {str(p.relative_to(source)): digest(p) for p in source.rglob('*')
                if p.is_file() and p.name != '传输身份.json'}
    (source / '传输身份.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    old = BASE / '源码/协同池副本/plugin/zenfs/fs'
    differences = []
    for path in sorted(fs.iterdir()):
        if not path.is_file(): continue
        before = (old / path.name).read_text() if (old / path.name).exists() else ''
        after = path.read_text()
        if before != after:
            differences.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                fromfile='冻结G2/' + path.name, tofile='W历史准入/' + path.name))
    # Flush计时共同修改也写入完整差异，避免遗漏计时开销的身份。
    name = '诊断-flush_job.cc'
    differences.extend(difflib.unified_diff((BASE / '源码' / name).read_text().splitlines(True),
        (source / name).read_text().splitlines(True), fromfile='冻结G2/' + name, tofile='W历史准入/' + name))
    (output / 'W历史准入差异.patch').write_text(''.join(differences))
    # 通用准备器会先生成旧实验的patch；新交付的两个入口名都必须指向真实新差异。
    (output / '代码差异.patch').write_text(''.join(differences))
    proof = dict(来源=str(BASE), 模式=['G2', MODE], 生产默认改变=False,
        磁盘编码改变=False, 新源码=identity['新源码'], 已运行完整业务=False,
        风险='普通区成本转移、后续追加及重挂载历史丢失；不能宣称已证明收益')
    (output / '副本身份.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    return proof


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=False, indent=2))
