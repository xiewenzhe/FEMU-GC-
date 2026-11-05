"""只给部署中的Flush添加默认关闭的分段时点；不改同步调用、锁或返回值。"""
from calibration_source import once
import re
import shlex
from pathlib import Path


def old_flush_extracted(mapping):
    # 只检查括号内真实archive成员；不能误把右侧引用来源的诊断对象名称当成员。
    members=mapping.split('Discarded input sections',1)[0]
    return any(Path(name).name=='flush_job.o' for name in
        re.findall(r'^librocksdb_debug\.a\(([^)\n]+)\)',members,re.M))


def native_command(dry):
    """提取Flush库对象本身的Make命令，不能复用db_bench工具的调试宏。"""
    matches=[]
    for line in dry.replace('\\\n',' ').splitlines():
        if '-c db/flush_job.cc' not in line:continue
        match=re.search(r'(?:^|&&\s+)((?:ccache\s+)?g\+\+\s+.*)$',line)
        if match:
            words=shlex.split(match[1])
            if '-o' in words:matches.append(words)
    if len(matches)!=1 or any(x in matches[0] for x in (';','&&','||','|','>')):
        raise ValueError('不能唯一提取Flush对象原生编译命令')
    return matches[0]


def patched(text):
    text=once(text,'#include <algorithm>','#include <algorithm>\n#include <chrono>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>')
    anchor='      s = BuildTable(\n'
    text=once(text,anchor,'''      // 诊断只记录时点；不跳过日志刷新、目录同步或数据库锁。
      const char* trace_flag=std::getenv("RRZC_CAL_FLUSH_TRACE");
      const bool trace_flush=trace_flag && !std::strcmp(trace_flag,"1");
      auto trace_phase=[&](const char* phase) {
        if (!trace_flush) return;
        const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        std::fprintf(stderr,"Flush分段：任务=%d，文件=%" PRIu64 "，阶段=%s，纳秒=%" PRIu64 "\\n",
            job_context_->job_id,meta_.fd.GetNumber(),phase,static_cast<uint64_t>(ns));
      };
      trace_phase("构建前");
      s = BuildTable(
''')
    # lambda后续要跨原花括号使用：记录值由外层同作用域的轻量状态保存。
    start=text.index('      // 诊断只记录时点；')
    end=text.index('      trace_phase("构建前");',start)
    declaration=text[start:end]
    text=text[:start]+text[end:]
    # 原函数在BuildTable前有嵌套作用域；将诊断lambda放到函数主体。
    function=text.index('Status FlushJob::WriteLevel0Table()')
    body=text.index('{',function)+1
    text=text[:body]+'\n'+''.join(line[4:] if line.startswith('    ') else line for line in declaration.splitlines(True))+text[body:]
    text=once(text,'      TEST_SYNC_POINT_CALLBACK("FlushJob::WriteLevel0Table:s", &s);',
        '      trace_phase("构建后");\n      TEST_SYNC_POINT_CALLBACK("FlushJob::WriteLevel0Table:s", &s);')
    text=once(text,'      LogFlush(db_options_.info_log);',
        '      trace_phase("日志刷新前");\n      LogFlush(db_options_.info_log);\n      trace_phase("日志刷新后");')
    text=once(text,'      s = output_file_directory_->FsyncWithDirOptions(',
        '      trace_phase("目录同步前");\n      s = output_file_directory_->FsyncWithDirOptions(')
    text=once(text,'          DirFsyncOptions(DirFsyncOptions::FsyncReason::kNewFileSynced));',
        '          DirFsyncOptions(DirFsyncOptions::FsyncReason::kNewFileSynced));\n      trace_phase("目录同步后");')
    text=once(text,'    TEST_SYNC_POINT_CALLBACK("FlushJob::WriteLevel0Table", &mems_);\n    db_mutex_->Lock();',
        '    TEST_SYNC_POINT_CALLBACK("FlushJob::WriteLevel0Table", &mems_);\n'
        '    trace_phase("数据库锁前");\n    db_mutex_->Lock();\n    trace_phase("数据库锁后");')
    return text
