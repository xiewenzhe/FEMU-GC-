"""从完整核验导出逐臂指标，不平均掩盖重复差异，不改原始归档。"""
import argparse
import csv
import json
from pathlib import Path
from ycsb_af import digest,require


def export(report,output):
    data=json.loads(report.read_text());root=Path(data['来源']);state=json.loads((root/'状态.json').read_text())
    require(state['完整实验完成'] and state['当前客机PID'] is None,'尚未完成退出')
    require(digest(Path(state['镜像处理']['archive']))==data['归档SHA256'],'原归档哈希不同')
    metrics=[];latency=[];phases=[]
    for tag,r in data['各臂'].items():
        metrics.append(dict(臂=tag,改选次数=r['改选次数'],**r['维护'],**r['资源'],**r['计时'],
            **{k:v for k,v in r['末端空间'].items() if k!='Zone'}))
        for x in r['API延迟']:
            latency.append(dict(臂=tag,操作=x['操作'],次数=x['次数'],平均微秒=x['平均纳秒']/1000,
                P99微秒=x['P99纳秒']/1000,P999微秒=x['P999纳秒']/1000,P9999微秒=x['P9999纳秒']/1000))
        for x in data['阶段'][tag]['阶段']:
            phases.append(dict(臂=tag,窗口=x['窗口'],请求=x['请求'],**x['本段维护'],
                累计迁移字节=x['维护累计']['总迁移字节'],空闲字节=x['空间']['空闲字节'],有效字节=x['空间']['有效字节']))
    output.mkdir(exist_ok=True,parents=True)
    for name,rows in [('维护与资源.csv',metrics),('API延迟.csv',latency),('阶段维护与空间.csv',phases)]:
        with (output/name).open('x',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,list(rows[0]));w.writeheader();w.writerows(rows)
    (output/'指标来源.json').write_text(json.dumps(dict(核验=str(report),核验SHA256=digest(report),
        原归档SHA256=data['归档SHA256'],导出源码SHA256=digest(Path(__file__)),
        单位='迁移和空间原始字节，API微秒，吞吐ops/s；分阶段空间在关闭前，末端资源在正常关闭后'),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('report',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();export(a.report,a.output)
