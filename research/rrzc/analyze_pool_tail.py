"""审计零有效尾部真实保留、复用与Reset；仅分析完整关闭/开启对照。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from analyze_all_blocks_dbbench import fields
from analyze_integrated_pool import REQUESTS
from analyze_pool_dbbench import repeat_guard
from ycsb_af import digest, require

ZONE=128<<20


def tail_events(path,enabled):
    source=path/'run.zenfs.log';rows=[];held={};counts=Counter();resets=[];conditions=Counter()
    for number,line in enumerate(source.read_text().splitlines(),1):
        if '协同尾部归零：' in line:
            x=fields(line,'协同尾部归零：');z=int(x['Zone']);keep=int(x['执行保留'])
            require(int(x['有效'])==0,'保留观察的有效数据非零')
            require(not keep or enabled,'关闭组执行保留')
            counts['归零检查']+=1;counts['符合条件检查']+=int(x['符合保留'])
            for key in ('Active','上限','等待','迁移中','预留可用','RC已知'):
                conditions[key+'='+x[key]]+=1
            if keep:
                require(x['RC已知']=='1' and int(x['RC'])<int(x['阈值']), '保留时读状态不安全')
                require(int(x['Active'])<int(x['上限']) and x['等待']=='0'
                    and x['迁移中']=='0' and x['预留可用']=='1', '保留时资源条件不符')
                require(z<int(x['WP'])<z+ZONE and int(x['剩余'])>0,'保留WP或尾部异常')
                counts['执行保留检查']+=1
                if z not in held:counts['新增保留周期']+=1;held[z]=dict(行=number,字段=x)
            rows.append(dict(行=number,事件='归零检查',字段=x))
        elif '协同尾部复用：' in line:
            x=fields(line,'协同尾部复用：');z=int(x['Zone'])
            require(z in held and enabled,'复用缺少同代际保留前件')
            previous=held.pop(z)
            require(int(x['WP'])==int(previous['字段']['WP']), '复用前WP与保留起点不同')
            require(x['RC不清零']=='1' and x['沿原WP追加']=='1','复用路径标记异常')
            counts['复用周期']+=1
            rows.append(dict(行=number,事件='复用分配',保留行=previous['行'],字段=x,
                限制='这是沿原WP的目标分配；内容及实际追加由真实设备安全检查另证'))
        elif '协同尾部退出：' in line:
            x=fields(line,'协同尾部退出：');require(x['成功']=='1','尾部退出Reset失败')
            counts['退出/'+x['原因']]+=1;rows.append(dict(行=number,事件='退出',字段=x))
        elif 'Zone 重置诊断：' in line:
            x=fields(line,'Zone 重置诊断：');require(x['状态']=='成功','原Reset失败')
            z=int(x['Zone']);wp=int(x['原写指针'])-z
            require(0<=wp<=ZONE,'Reset原WP不合法')
            counts['Reset满区' if wp==ZONE else 'Reset部分区']+=1
            if z in held:
                previous=held.pop(z);counts['保留后未复用就Reset']+=1
                rows.append(dict(行=number,事件='保留后Reset',保留行=previous['行'],字段=x))
            resets.append(dict(行=number,Zone=z//ZONE,WP推进字节=wp,字段=x))
        elif '协同尾部关闭清账：' in line:
            x=fields(line,'协同尾部关闭清账：');require(x['成功']=='1','关闭清账失败')
            counts['关闭清账成功']+=1
    require(not held,'关闭后仍存在有保留前件而无复用/Reset的尾部')
    require(counts['关闭清账成功']>0,'没有正常关闭清账证据')
    return dict(计数=dict(counts),观察条件分布=dict(conditions),事件=rows,Reset=resets,源日志SHA256=digest(source),
        口径='保留检查可能重复；周期按首次保留至复用分配或Reset计。WP推进包含Finish，不冒称实际编程量。')


def analyze(root,output):
    state=json.loads((root/'状态.json').read_text());stage=json.loads((root/'阶段归档.json').read_text())
    require(state.get('完整实验完成') and state['当前客机PID'] is None
        and stage['完整'] and stage['业务成功'],'尚未完整成功归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    require(not output.exists(),'不覆盖已有尾部分析')
    repeats={name:json.loads((root/(name+'重复核验.json')).read_text()) for name in ('关闭','开启')}
    require(all(x['通过'] for x in repeats.values()),'组内重复未通过')
    reports={tag:json.loads((root/(tag+'-核验.json')).read_text()) for tag in ('p1','p2','m1','m2')}
    cross={tag:repeat_guard(root/'逐臂采证/p1',root/'逐臂采证'/tag,request_count=REQUESTS)
        for tag in ('p2','m1','m2')}
    require(all(x['请求']['逻辑提交相同'] for x in cross.values()),'跨臂逻辑请求不同')
    tails={tag:tail_events(root/'逐臂采证'/tag,tag.startswith('m')) for tag in reports}
    for tag,r in reports.items():
        require(sum(tails[tag]['计数'].get(k,0) for k in ('Reset满区','Reset部分区'))
            ==r['维护']['全部成功Reset次数'],'Reset直接日志与维护账目不一致')
    output.mkdir(parents=True);rows=[];lat=[]
    for tag,r in reports.items():
        rows.append(dict(臂=tag,尾部复用=int(tag.startswith('m')),**r['维护'],**r['资源'],**r['计时'],
            新增保留周期=tails[tag]['计数'].get('新增保留周期',0),
            复用周期=tails[tag]['计数'].get('复用周期',0),
            末端空闲字节=r['末端空间']['空闲字节'],末端有效字节=r['末端空间']['有效字节']))
        lat.extend(dict(臂=tag,**x) for x in r['API延迟'])
    for name,data in (('维护与空间吞吐.csv',rows),('API延迟.csv',lat)):
        with (output/name).open('x',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,list(data[0]));writer.writeheader();writer.writerows(data)
    result=dict(通过=True,来源=str(root),各臂=reports,尾部事件=tails,跨臂比较=cross,
        重复=dict(Greedy=repeats['关闭'],机制=repeats['开启']),
        归档SHA256=state['镜像处理']['archive_sha256'],分析源码SHA256=digest(Path(__file__)),
        边界='两组同新程序K1+Greedy，只切换零有效尾部；固定种子两次工程重复，非正常并发/YCSB或统计显著性。')
    (output/'尾部对照核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    a,b=reports['p1'],reports['m1'];delta=b['维护']['总迁移字节']-a['维护']['总迁移字节']
    lines=['# 协同池零有效尾部复用：完整业务结果','',result['边界'],'',
        '两组均固定K1、执行Greedy，在线分类、原RR1024、GC20%和候选资格不变。新功能默认关闭，部署于独立ZenFS副本；原RocksDB库未覆盖。',
        '', '## 真实结果','', '|指标|关闭1|关闭2|开启1|开启2|','|---|---:|---:|---:|---:|']
    metrics=[('总维护迁移MiB','维护','总迁移字节',1/(1<<20)),
        ('ZC迁移MiB','维护','ZC迁移字节',1/(1<<20)),('RR迁移MiB','维护','RR迁移字节',1/(1<<20)),
        ('ZC次数','维护','ZC次数',1),('RR次数','维护','RR次数',1),
        ('成功Zone Reset','维护','全部成功Reset次数',1),
        ('普通Finish次数','资源','普通成功Finish次数',1),
        ('普通Finish尾损MiB','资源','普通Finish尾损字节',1/(1<<20)),
        ('末端空闲MiB','末端空间','空闲字节',1/(1<<20)),
        ('末端有效MiB','末端空间','有效字节',1/(1<<20)),
        ('末端Active','资源','末端ActiveZone数',1),('请求窗口吞吐ops/s','计时','请求窗口吞吐ops',1)]
    for label,group,key,scale in metrics:
        lines.append('|'+label+'|'+'|'.join(f'{r[group][key]*scale:.3f}' for r in reports.values())+'|')
    lines.extend(['',f'开启减关闭的业务维护复制差：{delta}字节（{delta/(1<<20):.3f}MiB，{delta/a["维护"]["总迁移字节"]*100:.3f}%）。',
        '每组两次逐条请求、起点、维护事件、复制—映射—Reset账目、末端及设备增量重复核验通过；每臂完整读回1048576条并校验。',
        '', '## 尾部路径证据',''])
    for tag,x in tails.items():lines.append(f'- {tag}：{json.dumps(x["计数"],ensure_ascii=False)}')
    lines.extend(['', '详见同目录`尾部对照核验.json`的原始行号、日志哈希和逐次前件关系。复用事件是分配入口证据；原WP真实追加、RC不清零、重挂载、有效内容和主动释放另由设备安全程序验证。',
        '', '## 如何解释','',
        '保留尾部使后续文件继续消耗原WP后的空间，减少提前Reset；保留期间Active名额和旧物理RC都保留。它改变后续布局，所以RR次数、每次伴随迁移和空间GC需求都可能改变。不能把RR减少全部当成净复制节省，也不能把变化归于victim模型，本轮全部执行Greedy。',
        f'本轮末端空闲差为{(b["末端空间"]["空闲字节"]-a["末端空间"]["空闲字节"])/(1<<20):.3f}MiB。正常关闭只清理保留的零有效尾部，不等于已经把两组恢复到相同可用空间。尚需相同空间目标的收尾账目，才能判断窗内复制差是否覆盖后续代价。',
        'Reset是直接成功Zone Reset日志计数，未直接采集物理Block擦除。原WP推进包含Finish，不等同实际写入字节。',
        '', '## API延迟（微秒）','', '|配置|操作|平均|P99|P99.9|P99.99|','|---|---|---:|---:|---:|---:|'])
    for tag,r in reports.items():
        for x in r['API延迟']:
            lines.append('|'+tag+'|'+x['操作']+'|'+'|'.join(f'{x[k]/1000:.3f}' for k in
                ('平均纳秒','P99纳秒','P999纳秒','P9999纳秒'))+'|')
    lines.extend(['','Put是关闭WAL、保留MemTable时的API耗时，不是NAND编程延迟。业务吞吐包含请求间的显式Flush和等待，读回不计入。两次工程重复的时间仍有波动，不据此声称正常并发性能或统计显著性提升。',
        '', '## 验证范围与收尾','',
        '实际五个ZenFS单元、同ABI客户端和探针编译通过。v1遗漏打包依赖、v2安全入口漏设置客机调度器，均在业务前失败并保留归档。v3设备安全检查的GC关闭进程还存在选区配置告警，不能当作选区测试；完整业务使用原完整参数、无ERROR并通过全部维护检查。工作区安全入口已补齐该参数及无ERROR检查，此入口末尾修正尚未再次实跑，不覆盖v3冻结源码。',
        '尚未验证崩溃恢复、正常并发下的新尾部逻辑和32GiB正式负载，默认关闭保持不变。',
        f'完整结果：`{root}`。归档SHA256：`{state["镜像处理"]["archive_sha256"]}`。客机已退出；临时镜像状态：{state["镜像处理"]["status"]}。固定逻辑输入引用原来源，不声称归档包含完整NAND运行快照。',
        '', '下一步先核算相同空间目标的收尾迁移和Reset，再决定是否启用尾部复用；继续冻结分类与victim算法，避免同时改变多个因素。'])
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({t:dict(维护=r['维护'],尾部=tails[t]['计数']) for t,r in reports.items()},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();analyze(a.root,a.output)
