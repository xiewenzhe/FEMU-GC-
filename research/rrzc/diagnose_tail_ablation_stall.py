#!/usr/bin/env python3
"""只读核验已归档的bypass停顿证据；不将未完成实验当性能结果。"""
import csv
import hashlib
import json
from pathlib import Path
import sys

from analyze_v1_shadow_stream import fields, require
from guarded_rounds import parse


def interleaving(lines, target, file_id):
    reopens = [i for i, s in enumerate(lines) if "Reopen writable file: "+target in s]
    require(reopens, "未找到目标文件重开")
    reopen = reopens[-1]
    starts = [i for i, s in enumerate(lines[:reopen]) if "MigrateFileExtents, fname: "+target in s]
    require(starts, "重开前没有目标迁移")
    # 同一文件已经搬过一次，必须取本次重开之前最近的迁移，不能拼接两轮。
    begin = starts[-1]
    copies = [i for i in range(reopen+1, len(lines)) if
              f"V1后续迁移复制：文件编号={file_id}，" in lines[i]]
    require(copies, "重开后没有未提交复制")
    copied = copies[0]
    require("复制成功=1" in lines[copied] and "映射提交=尚未确认" in lines[copied], "未完成复制证据不符")
    require(not any("ReleaseMigrateZone:" in s or f"V1后续映射提交：文件编号={file_id}，" in s or
        "MigrateFileExtents Finished, fname: "+target in s for s in lines[copied+1:]), "末次迁移实际已经完成")
    return begin, reopen, copied


def diagnose(directory):
    run = directory/"guarded-medium-20261015-guarded"
    verified = 0
    remote_hashes = {}
    for row in (directory/"客机原始文件.sha256").read_text().splitlines():
        digest, name = row.split(maxsplit=1)
        remote_hashes[name] = digest
        path = Path(name)
        if name.startswith("/home/femu/rrzc-stream-results/"):
            local = directory/path.relative_to("/home/femu/rrzc-stream-results")
        elif name.startswith("/home/femu/rrzc-"):
            local = directory/path.relative_to("/home/femu")
        elif "/plugin/zenfs/fs/" in name:
            local = directory/"客机源码"/path.name
        else:
            continue
        require(local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == digest,
                "归档与客机原始哈希不符："+str(local))
        verified += 1
    before = {name: digest for digest, name in (r.split() for r in
        (directory.parent.parent/"tail-ablation-medium-20261015-bypass/完整源码身份.before.sha256").read_text().splitlines())}
    for name in ("librocksdb_debug.a", "plugin/zenfs/fs/fs_zenfs.cc"):
        require(before[name] == remote_hashes[name], "实际库或接线源不再与业务前一致")
    require(not any((run/n).exists() for n in ("run.exit", "verify.exit", "runner.exit")), "不再符合未完成现场")
    lines = (run/"zenfs.run.log").read_text().splitlines()
    last_gc = max(i for i, s in enumerate(lines) if "GC 检查：" in s)
    decisions, _, maintenance = parse(lines[:last_gc], "guarded", "bypass")
    last = next(fields(s, "独立约束决策：") for s in reversed(lines) if "独立约束决策：" in s)
    require(last["轮次"] == "19" and last["原会改选"] == last["实际改选"] == "0" and
            last["实际Zone"] == last["GreedyZone"] == "805306368", "末轮现场与预期证据不符")
    target = "/rrzc-stream/g4.hot.anchor.sst"
    begin, reopen, copied = interleaving(lines, target, 39)
    fs = (directory/"客机源码/fs_zenfs.cc").read_text()
    io = (directory/"客机源码/io_zenfs.cc").read_text()
    opened = fs[fs.index("IOStatus ZenFS::OpenWritableFile("):fs.index("IOStatus ZenFS::DeleteFile(")]
    migrated = fs[fs.index("IOStatus ZenFS::MigrateFileExtents("):]
    require(opened.index("file_lock(files_mtx_)") < opened.index("zoneFile->AcquireWRLock();"), "重开锁序改变")
    require(migrated.index("zfile->TryAcquireWRLock()") < migrated.index("if (GetFile(fname).get() != zfile.get())") <
            migrated.index("zfile->ReleaseWRLock();"), "迁移锁序改变")
    require("open_for_wr_mtx_.lock();" in io and "std::lock_guard<std::mutex> lock(files_mtx_);" in
            fs[fs.index("std::shared_ptr<ZoneFile> ZenFS::GetFile("):fs.index("IOStatus ZenFS::DeleteFileNoLock(")],
            "锁实现与解释不符")
    probe = (directory/"真实文件锁内存验证.log").read_text()
    require(all(s in probe for s in ("相反锁序闭环条件成立=1", "释放文件锁后重开完成=1")), "内存诊断未通过")
    with (run/"operations.csv").open() as stream:
        operations = list(csv.DictReader(stream))
    tails = {}
    for thread in ("0", "1"):
        records = [r for r in operations if r["线程"] == thread]
        require([int(r["序号"]) for r in records] == list(range(len(records))), "业务已完成记录不连续")
        tails[thread] = records[-1]
    events = [dict(行号=i+1,原日志=lines[i]) for i in (begin, reopen, copied)]
    return dict(诊断证据核验通过=True,完整实验核验通过=False,原始归档哈希文件数=verified,
        已闭合选区轮次=len(decisions),已闭合ZC维护数=len(maintenance),
        已闭合实际改选次数=sum(d["独立核验改选"] for d in decisions),
        未闭合选区轮次=last,最后已完成业务操作=tails,关键交错事件=events,
        已确认代码锁序={"重开文件": "files_mtx_ → open_for_wr_mtx_（阻塞）",
                      "迁移文件": "open_for_wr_mtx_ → files_mtx_（GetFile）"},
        真实文件锁内存验证通过=True,
        最可能原因="迁移g4热文件与业务Reopen退役交错形成ABBA互等；代码存在闭环且日志吻合，原进程栈已缺失，不能声称现场栈证实。",
        监督器问题="超时异常前未归档或采栈；SSH伪终端的清理可能使远端业务退出。sudo会话04:07:31关闭，与监督器超时同秒，但未取得具体信号记录。",
        未完成验证=["原停顿进程线程栈", "实际SSD路径的可控复现", "修复后回归及完整配对"],
        已修改生产代码=False,已重跑业务=False,已重启客机=False)


if __name__ == "__main__":
    require(len(sys.argv) == 2, "输入本轮独立诊断目录")
    print(json.dumps(diagnose(Path(sys.argv[1])), ensure_ascii=False, indent=2))
