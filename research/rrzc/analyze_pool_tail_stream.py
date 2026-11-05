"""以真实日志字节切点核算连续阶段，不将文件字节偏移当作行为差异。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile

from audit_colocation_transactions import audit
from analyze_reproducibility import request_rows,initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from analyze_pool_tail import tail_events
from ycsb_af import digest,require

PHASES=(6,12,18,24)


def load(path):return json.loads(path.read_text())


def prefix_proof(old,new):
    target=iter(request_rows(new/'请求提交.bin'));n=0;h=hashlib.sha256()
    for row in request_rows(old/'请求提交.bin'):
        current=next(target,None)
        require(current is not None and row[:4]==current[:4],'旧请求前缀首差：'+str(n))
        h.update(struct.pack('<4Q',*row[:4]));n+=1
    require(n==2260992,'旧前缀未完整记录')
    start=initial(old,new);require(all(start[k]['相同'] for k in SEMANTIC),'延长负载起点不同')
    return dict(通过=True,已核验前缀请求=n,逻辑前缀SHA256=h.hexdigest(),起点=start,
        边界='只证明共同起点和原12窗口请求前缀；新增阶段观测改变观测开销，计时不与旧轮混用')


def phase_arm(root,full):
    raw=(root/'run.zenfs.log').read_bytes();reports=[];proof=[];last={}
    for windows in PHASES:
        snap=load(root/f'请求提交.bin.window-{windows}.json')
        require(snap['窗口']==windows and snap['检查点']==windows*10
            and snap['请求']==windows*188416,'阶段与预定逻辑工作量不符')
        end=snap['原日志字节切点'];require(0<end<=len(raw) and raw[end-1:end]==b'\n','日志切点不是完整行')
        marker=f'持续负载维护边界：检查点={windows*10}，请求={snap["请求"]}'
        require(marker in raw[:end].decode().splitlines()[-1],'切点没有实际Logger已Flush的阶段标记')
        with tempfile.TemporaryDirectory(prefix='rrzc-stream-ledger-') as tmp:
            p=Path(tmp);(p/'zenfs.log').write_text('受控放置业务开始：派生阶段窗\n'+raw[:end].decode()+'\n受控放置业务截止：派生阶段窗\n')
            tx=audit(p)
        ledger=tx['维护账目']
        keys=('总迁移字节','ZC迁移字节','RR迁移字节','ZC次数','RR次数','全部成功Reset次数')
        delta={k:ledger[k]-last.get(k,0) for k in keys}
        require(min(delta.values())>=0,'阶段累计计数倒退')
        layout=root/f'布局-{windows*10}.extent.csv';sst=root/f'布局-{windows*10}.sst.csv'
        require(layout.is_file() and sst.is_file(),'阶段映射缺失')
        reports.append(dict(窗口=windows,请求=snap['请求'],维护累计=ledger,本段维护=delta,
            空间={k:v for k,v in snap.items() if k not in ('原日志字节切点','窗口','检查点','请求')},
            extentSHA256=digest(layout),SST_SHA256=digest(sst)))
        proof.append(dict(窗口=windows,原日志字节切点=end,日志前缀SHA256=hashlib.sha256(raw[:end]).hexdigest()))
        last=ledger
    # 最后切点在数据库关闭之前，收尾可能仍有自然Reset，必须单列。
    cleanup={k:full['维护'][k]-last[k] for k in keys}
    require(min(cleanup.values())>=0,'阶段累计量超过完整账目')
    return dict(阶段=reports,关闭阶段维护=cleanup,证据=proof,
        边界='原GC确认后、无迁移活动且原日志字节数在采样期间不变；原日志及请求保留，阶段间未重挂载。')


def analyze(root,output):
    state=load(root/'状态.json');stage=load(root/'阶段归档.json')
    require(state.get('完整实验完成') and state['当前客机PID'] is None and stage['完整'] and stage['业务成功'],
        '尚未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    require(not output.exists(),'不覆盖已有持续对照报告')
    repeats={k:load(root/(k+'重复核验.json')) for k in ('关闭','开启')}
    require(all(x['通过'] for x in repeats.values()),'完整或阶段重复未通过')
    reports={t:load(root/(t+'-核验.json')) for t in ('p1','p2','m1','m2')};phases={}
    rows=[];lat=[];tails={}
    for tag,r in reports.items():
        require(load(root/(tag+'-旧请求前缀核验.json'))['通过'],'旧请求前缀不一致')
        log=(root/'逐臂采证'/tag/'run.zenfs.log').read_text()
        require(f'协同尾部配置：版本=1，执行={int(tag.startswith("m"))}，观测=1' in log,
            '实际尾部开关不符，不能发布对照')
        require('协同尾部关闭清账：成功=1' in log,'尾部正常关闭清账缺失')
        tails[tag]=tail_events(root/'逐臂采证'/tag,tag.startswith('m'))
        require(sum(tails[tag]['计数'].get(k,0) for k in ('Reset满区','Reset部分区'))
            ==r['维护']['全部成功Reset次数'],'尾部直接Reset与维护账目不符')
        phases[tag]=phase_arm(root/'逐臂采证'/tag,r)
        require(phases[tag]==load(root/(tag+'-阶段核验.json')),'归档后阶段重算不同')
        for x in phases[tag]['阶段']:
            rows.append(dict(臂=tag,窗口=x['窗口'],请求=x['请求'],
                **x['本段维护'],累计迁移字节=x['维护累计']['总迁移字节'],
                空闲字节=x['空间']['空闲字节'],有效字节=x['空间']['有效字节'],
                满区可回收字节=x['空间']['满区可回收字节']))
        lat.extend(dict(臂=tag,**x) for x in r['API延迟'])
    require(phases['p1']['阶段']==phases['p2']['阶段'] and phases['m1']['阶段']==phases['m2']['阶段'],
        '阶段维护、空间或映射存在实际分歧')
    # 组内重复和旧12窗口前缀之外，直接核验跨开关的完整24窗口输入及实际起点。
    cross=request_comparison(root/'逐臂采证/p1',root/'逐臂采证/m1')
    start=initial(root/'逐臂采证/p1',root/'逐臂采证/m1')
    require(cross['逻辑提交相同'] and cross['完整成功请求数']==[4521984]*2
        and all(start[k]['相同'] for k in SEMANTIC),'跨开关完整输入或起点不同')
    output.mkdir(parents=True)
    for name,data in [('分阶段维护与空间.csv',rows),('API延迟.csv',lat)]:
        with (output/name).open('x',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,list(data[0]));writer.writeheader();writer.writerows(data)
    result=dict(通过=True,各臂=reports,阶段=phases,尾部事件=tails,重复=repeats,
        跨开关完整请求=cross,跨开关起点=start,来源=str(root),
        分析器SHA256=digest(Path(__file__)),归档SHA256=state['镜像处理']['archive_sha256'],
        边界='固定种子两次工程重复、受控单前台及原批次维护；24窗口不等于已达到长期稳态或正常并发性能。')
    (output/'持续对照核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copyfile(Path(__file__),output/'分析源码.py')
    lines=['# 尾部复用：连续24窗口对照','',result['边界'],'',
        '两组固定K1＋Greedy，只切换尾部复用。每臂4521984次请求，同一进程连续运行，未在12窗口后关闭、Reset或重挂载，在线H历史保持。',
        '跨开关逐条核验全部4521984次操作类型、Key、序号及提交顺序；起点核验范围与未覆盖项见持续对照核验.json，不冒称完整NAND快照。',
        '', '|累计窗口|关闭累计迁移MiB|开启累计迁移MiB|关闭空闲MiB|开启空闲MiB|开启减关闭空闲MiB|',
        '|---|---:|---:|---:|---:|---:|']
    for a,b in zip(phases['p1']['阶段'],phases['m1']['阶段']):
        lines.append(f'|{a["窗口"]}|{a["维护累计"]["总迁移字节"]/(1<<20):.3f}|{b["维护累计"]["总迁移字节"]/(1<<20):.3f}|'
            f'{a["空间"]["空闲字节"]/(1<<20):.3f}|{b["空间"]["空闲字节"]/(1<<20):.3f}|'
            f'{(b["空间"]["空闲字节"]-a["空间"]["空闲字节"])/(1<<20):.3f}|')
    lines.extend(['','## 完整业务及关闭成本','', '|指标|关闭1|关闭2|开启1|开启2|','|---|---:|---:|---:|---:|'])
    for label,group,key,scale in [('总复制MiB','维护','总迁移字节',1<<20),
        ('ZC次数','维护','ZC次数',1),('RR次数','维护','RR次数',1),
        ('成功Reset','维护','全部成功Reset次数',1),('末端空闲MiB','末端空间','空闲字节',1<<20),
        ('末端有效MiB','末端空间','有效字节',1<<20),('请求窗口吞吐ops/s','计时','请求窗口吞吐ops',1)]:
        lines.append('|'+label+'|'+'|'.join(f'{r[group][key]/scale:.3f}' for r in reports.values())+'|')
    lines.extend(['','原始平均/P99/P99.9/P99.99读写API统计见API延迟.csv；无WAL的Put不是介质编程耗时。'
        '原日志切点、阶段数据及全部映射/账目保留，两次时点日志字节偏移可以不同，不冒称其为物理行为差异。',
        '本轮增加共同日志缓冲刷新和空间快照，不将计时与旧观察配置混用；没有强制GC、降低候选门槛或设定victim。'
        '只在相同累计逻辑工作量上比较；不同末端空闲仍须同时报告，不能仅看RR数量或截取最好阶段。',
        '',f'归档SHA256：`{result["归档SHA256"]}`。客机已退出；临时镜像状态：{state["镜像处理"]["status"]}。'])
    a,b=reports['p1'],reports['m1']
    early=[phases[t]['阶段'][1]['维护累计']['总迁移字节'] for t in ('p1','m1')]
    late=[reports[t]['维护']['总迁移字节']-early[n] for n,t in enumerate(('p1','m1'))]
    lines.extend(['','## 前后半程是否出现额外代价','',
        '|区间|关闭复制MiB|开启复制MiB|开启减关闭MiB|','|---|---:|---:|---:|',
        f'|前12窗口|{early[0]/(1<<20):.3f}|{early[1]/(1<<20):.3f}|{(early[1]-early[0])/(1<<20):.3f}|',
        f'|后12窗口及关闭|{late[0]/(1<<20):.3f}|{late[1]/(1<<20):.3f}|{(late[1]-late[0])/(1<<20):.3f}|',
        '此处直接使用实际阶段累计差，不是未来成本预测。第一组重复的维护/空间值与第二组相同；时间值仍逐臂保留。'])
    delta=b['维护']['总迁移字节']-a['维护']['总迁移字节']
    space=b['末端空间']['空闲字节']-a['末端空间']['空闲字节']
    if delta<0 and space>=0 and a['末端空间']['有效字节']==b['末端空间']['有效字节']:
        conclusion='本轮完整受控工作量下，开启组总复制更少且末端空间不低；仍需单列Reset及性能代价，不能推广为正式稳态结论。'
    elif delta>=0:
        conclusion='本轮完整工作量中，尾部开启没有减少总复制；不能只用前半程或Reset下降宣传复制收益。'
    else:
        conclusion='本轮总复制更少，但末端空闲空间也更少；空间债务仍须单列，不能宣称已验证等空间净收益。'
    lines.extend(['',conclusion,f'开启减关闭：总复制{delta/(1<<20):.3f}MiB，末端空闲{space/(1<<20):.3f}MiB。',
        '本轮双方均执行Greedy，没有改动victim模型；实际差异属于尾部放置管理及其后续路径。'])
    lines.extend(['','## 迁移分项与尾部真实路径','',
        '|指标|关闭1|关闭2|开启1|开启2|','|---|---:|---:|---:|---:|'])
    for label,group,key,scale in (
        ('ZC复制MiB','维护','ZC迁移字节',1<<20),('RR复制MiB','维护','RR迁移字节',1<<20),
        ('普通Finish次数','资源','普通成功Finish次数',1),
        ('普通Finish尾损MiB','资源','普通Finish尾损字节',1<<20),
        ('RR源封闭尾部MiB','资源','RR源封闭尾部字节',1<<20)):
        lines.append('|'+label+'|'+'|'.join(f'{r[group][key]/scale:.3f}' for r in reports.values())+'|')
    for tag,x in tails.items():lines.append(f'\n{tag}尾部计数：{json.dumps(x["计数"],ensure_ascii=False)}。')
    zc=b['维护']['ZC迁移字节']-a['维护']['ZC迁移字节']
    rr=b['维护']['RR迁移字节']-a['维护']['RR迁移字节']
    require(zc+rr==delta,'ZC与RR差额不能解释总差额')
    lines.extend(['',f'完整窗口开启减关闭：ZC复制{zc/(1<<20):.3f}MiB，RR复制{rr/(1<<20):.3f}MiB，'
        f'两项合计{delta/(1<<20):.3f}MiB。尾部路径按同代际保留前件核验，Reset分满区和部分区；'
        '少Reset不等于少复制，未把Zone Reset换算成实测物理块擦除。',
        '已确认的是尾部开关改变后续物理布局，并产生上述维护链和空间差；'
        '尚未逐次证明每一个额外ZC由哪一次保留造成，不把聚合差额冒称逐事件因果。',
        '本轮两次重复是同种子工程核验，不代表不同种子或正常并发下的收益分布。'
        '若完整窗口退化，默认关闭保持不变，先回到放置与victim选择主线，不继续用短窗RR减少作为启用理由。'])
    (output/'结果分析.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({t:r['维护'] for t,r in reports.items()},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();analyze(a.root,a.output)
