"""校准阶段分段计时与起跑门控核验；不将两次重复解释为统计显著性。"""
import json
from pathlib import Path
import re
import csv
from analyze_all_blocks_dbbench import arm,require,batches


def background_overlap(text):
    """从真实事件配对检查Flush和Compaction任务区间，不用线程配置代替执行证据。"""
    active={};overlaps=[];counts={'flush_started':0,'compaction_started':0}
    for line in text.splitlines():
        if 'EVENT_LOG_v1 ' not in line:continue
        event=json.loads(line.split('EVENT_LOG_v1 ',1)[1]);kind=event.get('event');job=event.get('job')
        if kind in counts:
            require(job not in active,'后台事件重复开始')
            if active:overlaps.append(dict(新任务=job,事件=kind,已有任务=dict(active)))
            active[job]=kind;counts[kind]+=1
        elif kind in ('flush_finished','compaction_finished'):
            require(active.get(job)==kind.replace('finished','started'),'后台完成事件缺少匹配开始')
            del active[job]
    require(not active,'后台事件未闭合')
    return dict(任务数=counts,重叠次数=len(overlaps),首个重叠=overlaps[:1])


def background_sequences(source):
    """去除墙钟、线程编号等标识，找出两轮真实后台内容序列的首个差异。"""
    events={}
    for tag in ('g1','g2'):
        events[tag]=[json.loads(line.split('EVENT_LOG_v1 ',1)[1])
            for line in (source/tag/'run.rocksdb.log').read_text().splitlines() if 'EVENT_LOG_v1 ' in line]
    fields={
        'flush_started':['num_memtables','total_num_input_entries'],
        'compaction_finished':['output_level','num_output_files','total_output_size','num_input_records','num_output_records'],
        'table_file_creation':['file_number','file_size','smallest_seqno','largest_seqno']}
    result={}
    for event,keys in fields.items():
        seq={t:[e for e in rows if e.get('event')==event] for t,rows in events.items()}
        first=None
        for i,(a,b) in enumerate(zip(seq['g1'],seq['g2'])):
            if any(a.get(k)!=b.get(k) for k in keys):
                first={'序号从零起':i,'g1':{k:a.get(k) for k in keys+['time_micros','job']},
                    'g2':{k:b.get(k) for k in keys+['time_micros','job']}}
                break
        result[event]=dict(数量={t:len(rows) for t,rows in seq.items()},首个内容差异=first,
            完全相同=first is None and len(seq['g1'])==len(seq['g2']))
    return result


def timing(root):
    text=(root/'run.log').read_text()
    marks=[(int(i),int(n)) for i,n in re.findall(r'校准阶段边界：序号=(\d+)，纳秒=(\d+)',text)]
    require([i for i,_ in marks]==list(range(6)), '六个阶段标记缺失、重复或乱序')
    times=[n for _,n in marks]
    require(times==sorted(times), '阶段时间倒退')
    rows=re.findall(r'RRZC批次完成：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)，开始纳秒=(\d+)，完成纳秒=(\d+)',text)
    batches(text)
    a,b=min(int(r[-2]) for r in rows),max(int(r[-1]) for r in rows)
    start,end=[int((root/name).read_text()) for name in ('run.start.ns','run.end.ns')]
    require(start<=times[0]<=times[1]<=a<=b<=times[2]<=times[3]<=times[4]<=times[5]<=end,'时序边界不闭合')
    out=dict(打开及开始前秒=(times[0]-start)/1e9,开始布局秒=(times[1]-times[0])/1e9,
        请求首尾秒=(b-a)/1e9,轮转准备及统计秒=(a-times[1]+times[2]-b)/1e9,
        Flush秒=(times[3]-times[2])/1e9,等待压缩秒=(times[4]-times[3])/1e9,
        结束布局秒=(times[5]-times[4])/1e9,关闭及退出秒=(end-times[5])/1e9)
    require(abs(sum(out.values())-(end-start)/1e9)<1e-6,'分段耗时之和不闭合')
    out.update(整阶段秒=(end-start)/1e9,请求窗口吞吐ops=1376256/((b-a)/1e9),
        整阶段吞吐ops=1376256/((end-start)/1e9),口径='等待压缩包含原生方法已有5秒唤醒等待；API耗时单独由Get/Put统计')
    return out


def analyze(source,output):
    data={}
    plan_path=source.parent/'预注册计划.json'
    plan=json.loads(plan_path.read_text()) if plan_path.exists() else {}
    for tag in ('g1','g2'):
        root=source/tag
        check=json.loads((root/'起始核验.json').read_text())
        require(check['通过'],'起始核验失败，不作效果比较')
        result=arm(root,'greedy',load_root=source/'source' if plan.get('固定逻辑输入') else None,
            records=plan.get('初始装载键数',1048576))
        if plan.get('共享单后台线程'):
            result['后台任务核验']=background_overlap((root/'run.rocksdb.log').read_text())
            require(result['后台任务核验']['重叠次数']==0,'后台串行配置没有生效')
        if plan.get('每次写后排空后台'):
            drain=re.findall(r'受控后台排空：Put次数=(\d+)，累计纳秒=(\d+)，不主动Flush=1',(root/'run.log').read_text())
            require(len(drain)==1 and int(drain[0][0])==589824,'后台排空计数不完整')
            result['后台排空等待秒']=int(drain[0][1])/1e9
        if plan.get('固定逻辑输入'):
            result['装载来源']='source/load.zenfs.log；一次共同装载，不是本臂重新执行的装载'
            result['初始输入回放']=json.loads((root/'逻辑输入回放.json').read_text())
            def loaded(prefix):
                with (root/(prefix+'.sst.csv')).open() as f:
                    ranges=sorted((r['文件'],r['层级'],r['最小键十六进制'],r['最大键十六进制']) for r in csv.DictReader(f))
                with (root/(prefix+'.extent.csv')).open() as f:
                    extents=sorted((r['文件'],r['Zone'],r['起点'],(int(r['字节'])+4095)//4096)
                        for r in csv.DictReader(f) if r['文件'].endswith('.sst'))
                return ranges,extents
            require(loaded('装载-0')==loaded('布局-0'),'回放后实际打开的LSM或SST extent位置与固定输入不一致')
            result['回放后实际LSM及SST逻辑extent核验通过']=True
            result['重开边界']='只核对原SST；重开RocksDB会正常更新CURRENT、OPTIONS等元数据，不声称进程内状态完全相同'
        if plan.get('前台固定全局顺序'):
            checkpoints=re.findall(r'全局回放检查点：批次=(\d+)，操作数=(\d+)，摘要=(\d+)',(root/'run.log').read_text())
            require(len(checkpoints)==24 and all(int(i)==n and int(count)==(n+1)*57344
                for n,(i,count,_) in enumerate(checkpoints)),'全局顺序检查点不完整')
            result['全局顺序检查点']=checkpoints
        if plan.get('GC批次检查门'):
            gc_log=(root/'run.zenfs.log').read_text()
            ticks=[int(n) for n in re.findall(r'受控GC检查点开始：批次=(\d+)',gc_log)]
            require(ticks==list(range(1,25)),'受控GC检查点未完整覆盖24批')
            require((root/'受控GC.request').read_text().strip()=='24' and
                    (root/'受控GC.ack').read_text().strip()=='24','受控GC请求或确认没有闭合')
            emergency=[(int(n),int(w)) for n,w in re.findall(
                r'受控GC紧急检查：上一完成批次=(\d+)，真实分配等待数=(\d+)',gc_log)]
            result['受控GC检查']=dict(批次检查=ticks,分配等待紧急检查=emergency,
                边界='紧急检查仅在真实Zone分配等待时允许，按原GC20%资格判断')
        if plan.get('内部随机性控制'):
            seeds=re.findall(r'内部随机数固定：线程=(\d+)，种子=(\d+)，请求随机数独立=1',
                (root/'run.log').read_text())
            require(sorted((int(t),int(s)) for t,s in seeds)==[(0,20365752),(1,20470481)],'真实读写线程内部种子未按计划固定')
        verify=(root/'verify.zenfs.log').read_text()
        require(not any(k in verify for k in ('[ERROR]','状态=失败','迁移目标内容类别冲突')),'重开维护异常')
        data[tag]=dict(阶段=timing(root),核验=result,起始门控=check)
    require(data['g1']['核验']['批次请求摘要']==data['g2']['核验']['批次请求摘要'],'请求摘要不一致')
    if plan.get('前台固定全局顺序'):
        require(data['g1']['核验']['全局顺序检查点']==data['g2']['核验']['全局顺序检查点'],'真实全局请求顺序摘要不一致')
    output.mkdir(exist_ok=False)
    (output/'核验.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (output/'后台序列比较.json').write_text(json.dumps(background_sequences(source),ensure_ascii=False,indent=2)+'\n')
    lines=['# 固定键段装载后的Greedy校准','',
        '只改变独立实验入口的装载与计时，原选区、RR1024及GC20%不改；两轮串行、相同观测。',
        '两次重复用于检查入口，不构成统计显著性。新装载过程不同于历史fillseq，不能将结果当成同负载模型收益。','',
        '| 指标 | Greedy① | Greedy② |','|---|---:|---:|']
    if plan.get('前台固定全局顺序'):
        lines[2:2]=['前台采用4读3写的固定全局顺序，API不重叠、没有QPS限速；此处吞吐和延迟只代表受控模式，不能作为原并发性能。','']
    if plan.get('固定逻辑输入'):
        lines[2:2]=['只装载一次，随后串行NVMe写回放同一逻辑输入；装载统计均来自source，业务统计来自各臂，不重复计算准备阶段。','']
    if plan.get('共享单后台线程'):
        lines[2:2]=['Flush与Compaction共用单后台线程，任务事件已核验无重叠；这是受控调度配置，不代表原双池并发性能。','']
    if plan.get('每次写后排空后台'):
        lines[2:2]=['每次Put后等待已触发的后台任务完成，之后再放行下一条前台请求；不主动Flush。该等待单独统计，不计入Put API延迟，计入请求窗口。','']
        for tag in ('g1','g2'):
            lines[2:2]=[f'{tag}累计后台排空等待：{data[tag]["核验"]["后台排空等待秒"]:.3f}秒。','']
    for key in data['g1']['阶段']:
        if key=='口径':continue
        lines.append(f'| {key} | {data["g1"]["阶段"][key]:.3f} | {data["g2"]["阶段"][key]:.3f} |')
    for name,key in [('ZC次数','ZC次数'),('RR次数','RR次数'),('维护迁移字节','总迁移字节')]:
        lines.append(f'| {name} | {data["g1"]["核验"]["业务维护"][key]} | {data["g2"]["核验"]["业务维护"][key]} |')
    lines+=['','请求窗口包含批次汇合，API耗时不含汇合；整阶段包含flush、压缩等待和关闭。',
        '起始门控核对键范围、数据页映射及空间；RC策略见起始门控，low仅要求两轮均在阈值1%以内，不冒称相同NAND状态。真实Reset代际和窗口信息保留，不要求跨轮代际编号相同。',
        '本轮尚未运行模型：两组观测完全同配置。进入模型性能对照前仍需统一全块采样和精简诊断日志，并单独报告机制计算开销。','']
    (output/'分析.md').write_text('\n'.join(lines))
    if plan.get('内部随机性控制'):
        a,b=[data[t]['阶段']['请求首尾秒'] for t in ('g1','g2')]
        x,y=[data[t]['核验']['业务维护']['总迁移字节'] for t in ('g1','g2')]
        ratio=max(a,b)/min(a,b);copy_ratio=max(x,y)/min(x,y)
        quality=dict(通过=ratio<=1.05 and copy_ratio<=1.05,请求时间较大较小比=ratio,
            总迁移较大较小比=copy_ratio,门槛=1.05,边界='两轮工程预检，不构成统计显著性；未通过时不得宣称低波动已修复')
        flushes={}
        for tag in ('g1','g2'):
            events=[json.loads(s.split('EVENT_LOG_v1 ',1)[1]) for s in (source/tag/'run.rocksdb.log').read_text().splitlines()
                    if 'EVENT_LOG_v1 ' in s]
            flushes[tag]=[[e['num_memtables'],e['total_num_input_entries']] for e in events if e['event']=='flush_started']
        quality['Flush序列完全相同']=flushes['g1']==flushes['g2']
        quality['Flush序列']=flushes
        (output/'重复性预检.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2)+'\n')
    return {tag:row['阶段'] for tag,row in data.items()}


if __name__=='__main__':
    import argparse
    import hashlib
    from analyze_greedy_variation import inspect
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    archive=json.loads((args.source.parent/'阶段归档.json').read_text())
    require(archive['完整'] and not archive['客机仍存活'],'仅复核已完整归档且退出的实验')
    analyze(args.source,args.output)
    detail={tag:inspect(args.source/tag) for tag in ('g1','g2')}
    (args.output/'等待与后台任务.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2)+'\n')
    hashes={}
    for tag in ('source','p1','p2','g1','g2'):
        for p in (args.source/tag).iterdir():
            if p.is_file() and p.suffix in ('.json','.log','.csv','.ns'):
                hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ('analyze_calibration.py','analyze_all_blocks_dbbench.py','analyze_greedy_variation.py'):
        p=Path(__file__).with_name(name);hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    (args.output/'来源SHA256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2)+'\n')
    print((args.output/'重复性预检.json').read_text().split('"Flush序列"',1)[0])
