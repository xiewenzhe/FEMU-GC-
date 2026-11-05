#!/usr/bin/env python3
"""对实际部署源生成挂载预留类别修复；不改RR、victim或磁盘格式。"""
import difflib
from pathlib import Path
from all_blocks_patch import once

ROOT=Path(__file__).resolve().parents[1]


def sources(fs,zbd,header,body=None):
    body=body if body is not None else (ROOT/'model/rrzc_reserve_restore.inc').read_text()
    marker='    Info(logger_,"公共内容隔离配置：启用=1，普通写入与迁移同规则=1，恢复重建=1，跨类别改名拒绝=1");'
    fs=once(fs,marker,'''    if (!readonly) {
      const auto restored=zbd_->ReconcileMigrationReserveContent();
      if (!restored.ok()) return Status::IOError("挂载预留类别恢复失败",restored.ToString());
    }
'''+marker)
    header=once(header,'  IOStatus Open(bool readonly, bool exclusive);',
        '  IOStatus Open(bool readonly, bool exclusive);\n  IOStatus ReconcileMigrationReserveContent();')
    zbd=once(zbd,'uint64_t ZonedBlockDevice::GetFreeSpace() {',body+'\nuint64_t ZonedBlockDevice::GetFreeSpace() {')
    return fs,zbd,header


def make_patch(fs,zbd,header):
    patch=''
    for name,old,new in zip(('fs_zenfs.cc','zbd_zenfs.cc','zbd_zenfs.h'),
                           (fs,zbd,header),sources(fs,zbd,header)):
        patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
                       fromfile='a/fs/'+name,tofile='b/fs/'+name))
    return patch
