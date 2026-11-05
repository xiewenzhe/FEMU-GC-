"""独立轮转负载接线和核验；原热点试次与旧模型保持不变。"""
import inspect
import json
from pathlib import Path
import re
import shutil

from dbbench_hotspot_suite import once,wrapper_source as hot_wrapper,analysis_source as hot_analysis
from dbbench_medium_stats import capture_source as medium_capture
from run_dbbench_parallel_host import ROOT,stamp
from ycsb_af import digest,require

ROUNDS=24
READS=786432
WRITES=589824


def wrapper_source():
    text=hot_wrapper(WRITES)
    text=once(text,'RRZC_DB_HOTSPOT="$hotspot"',
        'RRZC_ROTATE_ROUNDS="$rounds" RRZC_ROTATE_PHASE_BATCHES="$phase_batches" RRZC_DB_HOTSPOT="$hotspot"')
    text=once(text,'enabled=0; greedy=1;',
        'rounds=24; phase_batches=4\n[[ $tag != smoke-* ]] || { rounds=2; phase_batches=1; }\nenabled=0; greedy=1;')
    text=once(text,'--benchmarks=rrzclayout,readwhilewriting,rrzclayout',
        '--benchmarks=rrzclayout,rrzcrotating,rrzclayout')
    return once(text,'--read_random_exp_range=10 --finish_after_writes=1','')


def observe_rotating(state,data,phase):
    if phase!='run':return
    text=data.decode(errors='replace')
    for role,count in re.findall(r'RRZC批次完成：操作=(READ|WRITE)，批次=\d+，累计=(\d+)',text):
        state['进度']['run-线程'+('1' if role=='READ' else '0')]=int(count)
    for role,count in re.findall(r'RRZC调用延迟：操作=(READ|WRITE)，次数=(\d+)',text):
        state['确认完成'+('读' if role=='READ' else '写')+'次数']=int(count)
    if '轮转真实业务失败：' in text:state['错误']=(state['错误']+[text[-1000:]])[-5:]


def capture_source():
    text=once(medium_capture(),'            observe_bench(state,data,name)',
        '            observe_bench(state,data,name)\n            observe_rotating(state,data,name)')
    return once(text,"if __name__ == '__main__':",inspect.getsource(observe_rotating)+"\n\nif __name__ == '__main__':")


def api_histograms(text):
    pattern=r'RRZC调用延迟：操作=(READ|WRITE)，次数=(\d+)，平均纳秒=([\d.]+)，P99纳秒=(\d+)，P99.9纳秒=(\d+)，P99.99纳秒=(\d+)'
    rows=re.findall(pattern,text)
    require(len(rows)==2 and {r[0] for r in rows}=={'READ','WRITE'},'独立真实API耗时缺失或重复')
    return {r[0]:dict(次数=int(r[1]),平均微秒=float(r[2])/1000,
        **{k:float(v)/1000 for k,v in zip(('P99微秒','P99.9微秒','P99.99微秒'),r[3:])}) for r in rows}


def trace(text):
    rows=re.findall(r'RRZC批次完成：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)，开始纳秒=(\d+)，完成纳秒=(\d+)',text)
    result={}
    for role,round,count,base,checksum,begin,end in rows:
        key=(role,int(round));require(key not in result,'批次记录重复')
        require(int(end)>=int(begin),'批次时间倒退')
        result[key]=(int(count),int(base),int(checksum))
    return result


def analysis_source():
    text=hot_analysis(WRITES)
    text=once(text,"benchmark = 'readwhilewriting' if case == 'hot' else 'readrandomwriterandom'", "benchmark = 'rrzcrotating'")
    text=once(text,'latency = histograms(native)','latency = api_histograms(native)')
    text=once(text,"require(f'Exiting the writer after {writes} writes...' in native and\n"
        "                latency['READ']['次数'] == operations and stats['次数'] == operations, '并发读写总量不符')",
        "require(latency['READ']['次数']==operations and latency['WRITE']['次数']==writes and\n"
        "                stats['次数']==operations+writes,'轮转真实读写及原生汇总总量不符')")
    text=once(text,"原生直方图含基准循环开销；hot写线程不入合并直方图；并行非稳态，非裸IO",
        "独立READ/WRITE直方图只计真实Get/Put调用；业务吞吐含批次汇合及映射采样；串行定制轮转，非裸IO或公开标准负载")
    return text+'\n\n'+inspect.getsource(api_histograms)


def freeze(output):
    folder=output/'源码'
    for source in ROOT.glob('*.py'):shutil.copyfile(source,folder/source.name)
    for name in ('dbbench_rotating_suite.py','dbbench_rotating_source.py','rrzc_rotating.h',
                 'run_dbbench_rotating_host.py','dbbench_medium_stats.py','test_dbbench_rotating.py',
                 'dbbench_rotating_worker.py','dbbench_hotspot_suite.py','dbbench_suite.py','run_dbbench_parallel_host.py',
                 'capture_ycsb_stream_guest.py','strict_stats_guest.py'):
        shutil.copyfile(ROOT/'tests'/name,folder/name)
    (folder/'run_dbbench_guest.sh').write_text(wrapper_source())
    (folder/'capture_dbbench_guest.py').write_text(capture_source())
    (folder/'analyze_dbbench_rotating.py').write_text(analysis_source())
    expected=json.loads((output/'同库身份.json').read_text())
    target='/home/femu/'+output.name.removesuffix('-results')
    for name in ('run_dbbench_guest.sh','capture_dbbench_guest.py','capture_ycsb_stream_guest.py','strict_stats_guest.py'):
        expected[target+'/'+name]=digest(folder/name)
    stamp(output/'运行软件身份.json',expected)
    (folder/'software.sha256').write_text(''.join(h+'  '+n+'\n' for n,h in expected.items()))
