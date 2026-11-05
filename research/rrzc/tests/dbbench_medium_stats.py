"""中测只读分段采样；操作进度是日志报告下界，不是原子逐操作切点。"""
import inspect
from pathlib import Path

from dbbench_hotspot_suite import once


def observe_maintenance(state,data,phase):
    # 本函数被原样冻结进客机采样器，只使用其已有的re模块。
    box=state.setdefault('分阶段维护',{}).setdefault(phase,dict(
        RR次数=0,ZC次数=0,RR迁移字节=0,ZC迁移字节=0,成功Reset=0,
        实际评分决策数=0,异于同帧Greedy数=0))
    frame=state.setdefault('分段候选',{})
    for line in data.decode(errors='replace').splitlines():
        rr=re.search(r'RR 统计：尝试 \d+ 次，本次迁移 (\d+) 字节，本次重置 (\d+) 个 Zone',line)
        zc=re.search(r'ZC 统计：轮次 \d+，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone',line)
        for match,kind in ((rr,'RR'),(zc,'ZC')):
            if match:
                box[kind+'次数']+=int(match[2]);box[kind+'迁移字节']+=int(match[1])
        if re.search(r'Zone 重置诊断：Zone=\d+，状态=成功，',line):box['成功Reset']+=1
        if '统一成本：' in line:
            row=dict(s.split('=',1) for s in line.split('统一成本：',1)[1].strip().split('，'))
            key=phase+'/'+row['轮次']
            if frame.get('键')!=key:frame.clear();frame['键']=key
            if row['安全']=='1':
                item=[int(row['有效']),int(row['Zone'])]
                if '最少' not in frame or item<frame['最少']:frame['最少']=item
        for token in ('统一决策：','时序方案决策：'):
            if token in line:
                row=dict(s.split('=',1) for s in line.split(token,1)[1].strip().split('，'))
                box['实际评分决策数']+=1
                if row['实际存在']=='1' and frame.get('键')==phase+'/'+row['轮次'] and '最少' in frame:
                    box['异于同帧Greedy数']+=int(int(row['实际Zone'])!=frame['最少'][1])


def observe_bench(state,data,phase):
    if phase!='run':return
    text=data.decode(errors='replace')
    for value in re.findall(r'Exiting the writer after (\d+) writes',text):
        state['确认完成写次数']=int(value)
    for value in re.findall(r'^readwhilewriting\s+:.*? seconds (\d+) operations;',text,re.M):
        state['确认完成读次数']=int(value)


def capture_source():
    source=Path(__file__).with_name('capture_dbbench_guest.py').read_text()
    source=once(source,'def bench(data, name=name):',
        'def bench(data, name=name):\n            observe_bench(state,data,name)')
    source=once(source,'                for key, marker in MARKERS.items():',
        '                observe_maintenance(state,data,name)\n                for key, marker in MARKERS.items():')
    source=once(source,"错误=state['错误'], **{k:state[k] for k in MARKERS})",
        "错误=state['错误'], 分阶段维护=state.get('分阶段维护',{}),\n"
        "                确认完成读次数=state.get('确认完成读次数',0),\n"
        "                确认完成写次数=state.get('确认完成写次数',0), **{k:state[k] for k in MARKERS})")
    helpers='\n\n'+inspect.getsource(observe_maintenance)+'\n\n'+inspect.getsource(observe_bench)+'\n\n'
    return once(source,"if __name__ == '__main__':",helpers+"if __name__ == '__main__':")


def operation_count(point):
    # 已核对原生源码：线程0为写者，线程1为读者；完成行补足最后一次进度报告。
    counts=point['进度']
    return max(counts.get('run-线程0',0),point.get('确认完成写次数',0))+\
        max(counts.get('run-线程1',0),point.get('确认完成读次数',0))


def segment_rows(points,total):
    """记录首次观测到每百万操作的采样点，保留越界量，不插值伪造精确分段。"""
    rows=[];next_mark=1000000;previous=None
    for saved in points:
        p=saved['观测'];count=operation_count(p)
        if count<next_mark:continue
        run=p.get('分阶段维护',{}).get('run',{})
        row=dict(目标累计操作=next_mark,观测累计操作下界=count,UTC=saved['UTC'],
            **{k:run.get(k,0) for k in ('RR次数','ZC次数','RR迁移字节','ZC迁移字节',
                                      '成功Reset','实际评分决策数','异于同帧Greedy数')})
        row['总迁移字节']=row['RR迁移字节']+row['ZC迁移字节']
        row['本段观测操作差']=count-(previous['观测累计操作下界'] if previous else 0)
        row['本段迁移字节差']=row['总迁移字节']-(previous['总迁移字节'] if previous else 0)
        rows.append(row);previous=row
        # 同一次轮询跨过多个目标时只写一个实测点，不重复复制维护量冒充多个切点。
        next_mark=(count//1000000+1)*1000000
        if count>=total:break
    return rows
