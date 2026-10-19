"""复用原完整审计，并额外确认两个阶段实际采用相同L0反压配置。"""
import re
from analyze_v1_shadow_stream import require
from analyze_ycsb_overlap import analyze


def check_options(text):
    for key,value in (('level0_file_num_compaction_trigger','2'),('level0_slowdown_writes_trigger','4'),('level0_stop_writes_trigger','8')):
        require(set(re.findall(r'Options\.'+key+r':\s*(\d+)',text))=={value},'反压配置未唯一生效：'+key)


def analyze_backpressure(root):
    for phase in (root/'warmup',root):
        props=dict(s.split('=',1) for s in (phase/'workload.properties').read_text().splitlines()
            if '=' in s and not s.startswith('#'))
        require(props.get('rocksdb.level0_slowdown_writes_trigger')=='4' and
            props.get('rocksdb.level0_stop_writes_trigger')=='8','阶段反压配置不符')
    logs=[p.read_text(errors='replace') for p in (root/'aux').rglob('LOG*') if p.is_file()]
    check_options('\n'.join(logs))
    require(sum(len(re.findall(r'Options\.level0_stop_writes_trigger:\s*8\b',text)) for text in logs)==2,
        '没有分别确认两次数据库打开时的反压配置')
    result=analyze(root)
    result['公共反压配置']=dict(压实触发=2,写入减速=4,写入暂停=8,
        边界='仅本轮实验配置，未来机制配对各组必须统一；不是stock基线，不改RR/GC/候选资格或模型公式')
    return result
