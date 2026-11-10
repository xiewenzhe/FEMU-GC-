"""G2与W迁移门控的同口径分析；严格执行原迁移/请求账目，不预测收益。"""
import csv
import json
import re
import struct
from pathlib import Path
import analyze_phase_time
from analyze_placement_business import analyze as original_analyze,compare
from analyze_phase_time import merge
from ycsb_af import require

MODE='G2_WMIG_OFF'

def analyze(root,mode):
    # 新增14只描述两臂共同真实目录同步调用；原13类计时语义不改。
    analyze_phase_time.KINDS[14]='aux目录同步实际调用'
    report=original_analyze(root,mode)
    log=(root/'run.zenfs.log').read_text()
    require('放置消融配置：模式='+mode+'，' in log,'配置模式未精确匹配')
    skips=re.findall(r'W迁移准入回退：模式=G2_WMIG_OFF，对象=(\d+)，类别=(\d+)，需要字节=(\d+)，路径=必要迁移',log)
    if mode==MODE:
        require(report['放置事件']['Wonly必要迁移入池']==0,'新模式W-only必要迁移仍入池')
        require(all(int(tag)==2 for _,tag,_ in skips),'门控误拒绝H或HW')
    else:require(mode=='G2' and not skips,'基准出现新准入门控')
    raw=(root/'等待区间.bin').read_bytes();records=list(struct.iter_unpack('<6Q',raw[16:]));aux=[r for r in records if r[0]==14]
    require(len(aux)==720,'业务720次真实aux目录同步调用缺失')
    groups={}
    lines=(root/'run.log').read_text().splitlines()
    for lineno,line in enumerate(lines,1):
        m=re.search(r'Flush分段：任务=(\d+)，文件=(\d+)，阶段=(.*?)，纳秒=(\d+)',line)
        if m:groups.setdefault(int(m[1]),{})[m[3]]=(int(m[4]),lineno)
    details=[]
    for _,a,b,tid,job,_ in aux:
        marks=groups[job];left=marks['目录同步前'];right=marks['目录同步后']
        require(left[0]<=a<=b<=right[0],'aux实际调用未位于阶段标记内')
        details.append(dict(任务=job,线程=tid,开始纳秒=a,结束纳秒=b,实际调用纳秒=b-a,
            阶段包围纳秒=right[0]-left[0],包围额外纳秒=right[0]-left[0]-(b-a),前标记行=left[1],后标记行=right[1]))
    report['W迁移准入']=dict(拒绝次数=len(skips),事件=[dict(对象=int(o),标签=int(t),需要字节=int(n)) for o,t,n in skips])
    report['aux实际调用']=dict(次数=len(aux),调用累计秒=sum(r[2]-r[1] for r in aux)/1e9,
        区间并集秒=sum(b-a for a,b in merge([(r[1],r[2]) for r in aux]))/1e9,
        阶段包围秒=sum(d['阶段包围纳秒'] for d in details)/1e9,
        包围额外秒=sum(d['包围额外纳秒'] for d in details)/1e9,逐次=details,
        边界='内存计时包围原FsyncWithDirOptions；保持调用/返回处理。包含调用内调度，不是内核纯fsync耗时；未采集fd和块层完成。')
    return report


def finish(output):
    modes=('G2',MODE);reports={m:json.loads((output/m/'业务核验.json').read_text()) for m in modes}
    local={m:output/m/json.loads((output/m/'状态.json').read_text())['标签']/'p1' for m in modes}
    paired=compare(local['G2'],local[MODE]);dest=output/'配对分析';dest.mkdir()
    rows=[];lat=[];api=[]
    for m,r in reports.items():
        rows.append(dict(模式=m,**r['维护及资源']['维护'],**r['维护及资源']['资源'],
            吞吐ops=r['应用端到端']['应用完成吞吐ops'],末端有效字节=r['维护及资源']['末端空间']['有效字节'],
            末端可写字节=r['维护及资源']['末端空间']['空闲字节'],末端满区可回收字节=r['维护及资源']['末端空间']['满区可回收字节'],
            W迁移门控次数=r['W迁移准入']['拒绝次数'],H必要迁移入池=r['放置事件']['H必要迁移入池'],Wonly必要迁移入池=r['放置事件']['Wonly必要迁移入池'],
            aux实际调用秒=r['aux实际调用']['调用累计秒'],aux阶段包围额外秒=r['aux实际调用']['包围额外秒']))
        lat.extend(dict(模式=m,**v) for v in r['应用端到端']['指标']);api.extend(dict(模式=m,**v) for v in r['时间']['API延迟'])
    for name,data in [('维护资源吞吐.csv',rows),('端到端延迟.csv',lat),('API延迟.csv',api)]:
        with (dest/name).open('x',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,list(data[0]));w.writeheader();w.writerows(data)
    result=dict(通过=True,起点及请求=paired,
        总迁移变化百分比=(rows[1]['总迁移字节']/rows[0]['总迁移字节']-1)*100 if rows[0]['总迁移字节'] else None,
        末端有效量相同=rows[0]['末端有效字节']==rows[1]['末端有效字节'],
        边界='一对同程序独立客机；三模式历史结果仅作来源，不混用计时。旧W回退普通区可能转移成本，不能仅看RR次数。')
    (dest/'完整核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    text=['# G2与W-only迁移准入配对结果','',result['边界'],'','|指标|G2|G2_WMIG_OFF|','|---|---:|---:|']
    for name,key,unit in [('总迁移MiB','总迁移字节',1<<20),('RR迁移MiB','RR迁移字节',1<<20),('ZC迁移MiB','ZC迁移字节',1<<20),('RR次数','RR次数',1),('ZC次数','ZC次数',1),('吞吐ops/s','吞吐ops',1),('末端可写MiB','末端可写字节',1<<20),('aux实际调用秒','aux实际调用秒',1)]:
        text.append('|'+name+'|'+'|'.join(f'{r[key]/unit:.3f}' for r in rows)+'|')
    text+=['','总迁移变化百分比：'+str(result['总迁移变化百分比'])+'。末端有效量相同：'+str(result['末端有效量相同'])+'。',
        '没有按跳过目标数量推导节省字节；回收、复制和末端空间须整体判断。aux尚无系统调用/块层归因，不以本轮单对性能宣称全面优势。']
    (dest/'结果分析.md').write_text('\n'.join(text)+'\n');return result
