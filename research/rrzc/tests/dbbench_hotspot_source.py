"""从已归档真实源生成最小db_bench补丁；每个替换点必须唯一，不修改库。"""
import difflib
from ycsb_af import require


def once(text,before,after):
    require(text.count(before)==1,'实际db_bench源结构改变：'+before[:70])
    return text.replace(before,after,1)


def patched(text):
    # 当前复用库不导出测试SyncPoint符号；仅客户端采用发布断言配置，库不重编译。
    text='#ifndef NDEBUG\n#define NDEBUG 1\n#endif\n'+text
    # 保持原生测试宏及压缩头的包含顺序，新增ZenFS诊断头放在原头之后。
    text=once(text,'#include "util/zipf.h"','#include "util/zipf.h"\n#include "tools/rrzc_dbbench_hotspot.h"')
    text=once(text,'  int64_t GetRandomKey(Random64* rand) {\n',
        '  int64_t GetRandomKey(Random64* rand) {\n'
        '    if(rrzc_dbbench_hotspot::Enabled())return rrzc_dbbench_hotspot::Read(rand->Next(),FLAGS_num);\n')
    first=text.index('  void BGWriter(');last=text.index('\n  void ',first+10)
    section=text[first:last]
    section=once(section,'      GenerateKeyFromInt(thread->rand.Next() % FLAGS_num, FLAGS_num, &key);',
        '      const auto random=thread->rand.Next();\n'
        '      GenerateKeyFromInt(rrzc_dbbench_hotspot::Enabled()?rrzc_dbbench_hotspot::Write(random,FLAGS_num):random%FLAGS_num, FLAGS_num, &key);')
    marker='      bytes += key.size() + val.size() + user_timestamp_size_;\n      thread->stats.FinishedOps(&db_, db_.db, 1, kWrite);'
    section=once(section,marker,marker+'\n'
        '      if(rrzc_dbbench_hotspot::Enabled() && written%65536==0)\n'
        '        rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0);')
    text=text[:first]+section+text[last:]
    text=once(text,'      } else if (name == "sstables") {',
        '      } else if (name == "rrzclayout") {\n'
        '        rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0);\n'
        '      } else if (name == "sstables") {')
    return text


def patch(text):
    return ''.join(difflib.unified_diff(text.splitlines(True),patched(text).splitlines(True),
        fromfile='a/tools/db_bench_tool.cc',tofile='b/tools/db_bench_tool.cc'))
