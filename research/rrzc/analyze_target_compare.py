"""先核验双目标四臂可比性及执行规则，再输出账目；不把分配小测称为模型收益。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from analyze_pair_admission import fields
from analyze_pool_dbbench import SEMANTIC
from analyze_reproducibility import initial, request_comparison
from ycsb_af import digest, require


def decisions(path, enforce):
    rows=[]
    for n,line in enumerate(path.read_text().splitlines(),1):
        if '双目标比较决定：' not in line:continue
        f=fields(line,'双目标比较决定：');get=lambda k:int(f[k])
        require(get('序号')==len(rows)+1 and get('执行')==enforce,'比较序号或执行开关错误')
        require(get('实际普通')==int(bool(enforce and get('建议普通'))),'实际执行与建议不符')
        if get('建议普通'):
            require(get('可比') and get('普通存在') and get('普通风险安全'),'选择了不可验证的替代目标')
            require(get('协同有效')==get('协同账目') and get('普通有效')==get('普通账目') and
                    get('协同H')<=get('协同账目') and get('普通H')<=get('普通账目'),'有效量口径非法')
            require(get('普通账目')-get('普通H')<get('协同账目')-get('协同H') and
                    get('普通剩余')<=get('协同剩余'),'没有满足两条件支配仍替代')
        rows.append(dict(原始行=n,**f))
    require(rows,'没有发生必要H迁移比较')
    return rows,dict(比较次数=len(rows),可比较次数=sum(r['可比']=='1' for r in rows),
                     建议普通次数=sum(r['建议普通']=='1' for r in rows),
                     直接选择普通次数=sum(r['实际普通']=='1' for r in rows),
                     原因计数=dict(Counter(r['原因'] for r in rows)))


def write_csv(path,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('x',newline='') as out:
        writer=csv.DictWriter(out,keys);writer.writeheader();writer.writerows(rows)


def analyze(root,output):
    require(not output.exists(),'不覆盖已有分析')
    state=json.loads((root/'状态.json').read_text());arch=json.loads((root/'阶段归档.json').read_text())
    require(state['双目标比较对照'] and arch['完整'] and arch['业务成功'] and
            not arch['客机仍存活'] and state['当前客机PID'] is None,'未完成双目标对照归档退出')
    archive=state['镜像处理'];require(digest(Path(archive['archive']))==archive['archive_sha256'],'归档哈希改变')
    for name in ('原放置Greedy','双目标Greedy'):
        require(json.loads((root/(name+'重复核验.json')).read_text())['通过'],'同配置重复未通过')
    build=json.loads((root/'生命周期构建核验.json').read_text())
    require(build['双目标比较版本']==1 and build['ZenFS完整五单元重编'] and
            build['交错小测逐条请求通过'],'同ABI构建未验收')
    summaries={};rows=[];metrics=[];latencies=[];starts={}
    for arm in ('p1','p2','m1','m2'):
        path=root/'逐臂采证'/arm;log=(path/'run.zenfs.log').read_text()
        require('配对准入配置：启用=0，采证=1' in log,'混入旧配对拒绝')
        a,b=decisions(path/'run.zenfs.log',int(arm.startswith('m')))
        rows.extend(dict(臂=arm,**x) for x in a);summaries[arm]=b
        report=json.loads((root/(arm+'-核验.json')).read_text())
        require(report['改选次数']==0,'本轮应全部使用Greedy')
        metrics.append(dict(臂=arm,**report['维护'],**report['计时'],
                            **{k:v for k,v in report['末端空间'].items() if k!='Zone'}))
        latencies.extend(dict(臂=arm,**x) for x in report['API延迟'])
        if arm!='p1':
            start=initial(root/'逐臂采证/p1',path);request=request_comparison(root/'逐臂采证/p1',path)
            require(all(start[k]['相同'] for k in SEMANTIC) and request['逻辑提交相同'] and
                    request['完整成功请求数']==[1130496,1130496],'跨臂逻辑输入或已采集起点不同')
            starts[arm]=dict(起点=start,请求=request)
    output.mkdir(parents=True)
    write_csv(output/'逐次双目标比较.csv',rows);write_csv(output/'维护空间与吞吐.csv',metrics)
    write_csv(output/'API延迟.csv',latencies)
    result=dict(通过=True,来源=str(root),完整归档SHA256=archive['archive_sha256'],构建=build,
                各臂=summaries,跨臂=starts,分析源码SHA256=digest(Path(__file__)),
                边界='必要H迁移分配验证；四臂Greedy，原新文件及GC/RR选源保持；无ZC时不评价联合victim收益；保留原空间末端及耗时波动。')
    (output/'双目标核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(analyze(args.source,args.output)['各臂'],ensure_ascii=False,indent=2))
