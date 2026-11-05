"""补充固定H筛查的真实映射与资格链；不把被排除Zone冒充模型候选。"""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
from pool_dbbench_guest import table,key
from ycsb_af import digest,require

ZONE=128<<20


def analyze(output):
    proof_path=output/'生命周期机会核验.json';proof=json.loads(proof_path.read_text())
    state=json.loads((output/'状态.json').read_text());archive=json.loads((output/'阶段归档.json').read_text())
    require(proof['通过'] and proof['重复']['通过'] and archive['完整'] and archive['业务成功'] and
        not archive['客机仍存活'] and state['当前客机PID'] is None,'只分析完整退出及重复通过的新轮')
    require(digest(Path(state['镜像处理']['archive']))==proof['完整归档SHA256'],'完整归档哈希不同')
    require(all(a['固定H'] for a in proof['各臂'].values()),'仅适用于预注册固定H模式')
    rr_rows=[];mapping=[];summary={}
    for tag,arm in proof['各臂'].items():
        root=output/'逐臂采证'/tag
        # 按真实起点键边界定位H0原SST，不把所有历史H标签都当作本轮热读。
        h0={r['文件'] for r in table(root/'布局-0.sst.csv')
            if key(r['最小键十六进制'])<4096 and key(r['最大键十六进制'])>=0}
        require(h0,'缺少实际H0 SST')
        for phase in arm['阶段布局']:
            by_zone={}
            for e in table(root/f"布局-{phase['布局']}.extent.csv"):
                if e['文件'].removeprefix('/rrzc-dbbench') in h0:
                    zone=int(e['Zone'])//ZONE;by_zone[zone]=by_zone.get(zone,0)+int(e['字节'])
            mapping.append(dict(实验臂=tag,阶段=phase['布局'],H0原SST='/'.join(sorted(h0)),
                H0当前Zone及字节=json.dumps(by_zone,ensure_ascii=False,sort_keys=True),
                原W存活=phase['原W存活'],新失效W=len(phase['新失效W'])))
        excluded=[]
        for index,event in enumerate(arm['真实RR'],1):
            prior=event['最近同代际GC观察'];qualification=prior.get('资格') if prior else None
            dt=None
            if prior:
                parse=lambda t:datetime.strptime(t,'%Y/%m/%d-%H:%M:%S.%f')
                dt=(parse(event['时刻'])-parse(prior['时刻'])).total_seconds()
            row=dict(实验臂=tag,RR序号=index,ZoneID=event['Zone']//ZONE,
                日志Reset序数=event['本日志Reset序数'],RR开始时刻=event['时刻'],RR行=event['行'],
                已有同代际GC观察=bool(prior),GC观察行=prior['行'] if prior else '',
                GC观察最大RC=prior['RC'] if prior else '',距RR秒=dt if dt is not None else '',
                GC观察有效字节=qualification['有效字节'] if qualification else '',
                原无效比例门槛=qualification['原比例门槛'] if qualification else '',
                原比例资格=qualification['原比例资格'] if qualification else '',
                实际比例资格=qualification['实际比例资格'] if qualification else '')
            rr_rows.append(row)
            if qualification and not qualification['实际比例资格']:excluded.append(row)
        summary[tag]=dict(H0原SST=sorted(h0),被原空间资格排除后RR=excluded,
            合格候选随后RR快照=arm['代际内候选随后RR快照'])
    destination=output/'固定H诊断';require(not destination.exists(),'不覆盖派生诊断');destination.mkdir()
    for name,rows in [('逐次RR与候选资格.csv',rr_rows),('H0逐阶段位置.csv',mapping)]:
        with (destination/name).open('x',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    result=dict(通过=True,各臂=summary,输入=str(proof_path),输入SHA256=digest(proof_path),
        完整归档SHA256=proof['完整归档SHA256'],源码SHA256=digest(Path(__file__)),
        边界='同Zone同Reset代际的最近GC观察，不将两个Zone最大RC差当同一物理块增量；H0文件可继续存在但物理映射会移动。被排除后RR不等于改选可净省该次复制。')
    (destination/'固定H诊断.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('results',type=Path)
    result=analyze(parser.parse_args().results)
    print(json.dumps(result,ensure_ascii=False,indent=2))
