"""从RocksDB实际打开日志核验读缓存关闭；不修改设备或数据库。"""
import json
from pathlib import Path
import re
import sys


def check(text,opening_threads=None):
    patterns={
        'BlockCache关闭':(r'^[ \t]*no_block_cache:[ \t]*(\S+)',{'1'}),
        'BlockCache为空':(r'^[ \t]*block_cache:[ \t]*(\S+)',{'(nil)','nullptr'}),
        '持久缓存为空':(r'^[ \t]*persistent_cache:[ \t]*(\S+)',{'(nil)','nullptr'}),
        'RowCache为空':(r'Options\.row_cache:[ \t]*(\S+)',{'None'}),
        '直接读':(r'Options\.use_direct_reads:[ \t]*(\S+)',{'1'}),
        'Flush及Compaction直接写':(r'Options\.use_direct_io_for_flush_and_compaction:[ \t]*(\S+)',{'1'}),
    }
    result={}
    for name,(pattern,allowed) in patterns.items():
        values=re.findall(pattern,text,re.M)
        result[name]=bool(values) and all(v in allowed for v in values)
    # 同一日志可能有多次打开，不能只凭某次关闭而忽略后续启用。
    assert all(result.values()),'缓存或Direct I/O实际配置未通过：'+str(result)
    if opening_threads is not None:
        values=re.findall(r'Options\.max_file_opening_threads:[ \t]*(\d+)',text)
        assert values and all(int(v)==opening_threads for v in values),'实际打开文件线程数与计划不符'
        result['打开文件线程数已核验']=opening_threads
    result['边界']='核验RocksDB读缓存及Direct I/O；MemTable、表读取器元数据及文件句柄不属于此项关闭范围'
    return result


if __name__=='__main__':
    print(json.dumps(check(Path(sys.argv[1]).read_text(),int(sys.argv[2]) if len(sys.argv)>2 else None),ensure_ascii=False,indent=2))
