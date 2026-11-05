"""用冻结评分核核验已归档机会；诊断性改输入不代表新策略轨迹或收益。"""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import re
import tempfile

from analyze_all_blocks_dbbench import fields
from replay_maintenance_cover import snapshots,evaluate
from ycsb_af import digest,require

ZONE=128<<20


def audit(source,output):
    state=json.loads((source/'状态.json').read_text());archive=json.loads((source/'阶段归档.json').read_text())
    proof=json.loads((source/'生命周期机会核验.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None
        and proof['通过'] and proof['重复']['通过'],'来源未完整验收退出')
    require(digest(Path(state['镜像处理']['archive']))==proof['完整归档SHA256'],'归档不同')
    frozen=source/'源码/宿主评分核';identity=json.loads((frozen/'编译身份.json').read_text());binary=frozen/'维护核重算'
    require(digest(binary)==identity['二进制SHA256'],'冻结评分程序不同')
    for name,sha in identity['头文件'].items():require(digest(frozen/name)==sha,'冻结依赖不同')
    require(not output.exists(),'不覆盖输出');results={}
    for tag in ('p1','p2'):
        root=source/'逐臂采证'/tag;log=(root/'run.zenfs.log').read_text();lines=log.splitlines()
        with tempfile.TemporaryDirectory(prefix='rrzc-real-snapshot-') as tmp:
            derived=Path(tmp);(derived/'zenfs.log').write_text('受控放置业务开始：派生run窗口\n'+log+'\n受控放置业务截止：派生run窗口\n')
            records=[r for r in snapshots(derived) if r['轮次']==12]
        require(len(records)==1,'关键轮次缺失或重复');original=records[0]
        baseline=evaluate(binary,[original])[0]
        require(baseline['新建议']['Zone']==original['原实际决策']['模型Zone']==7*ZONE,'原建议不能重现')
        target=next(r for r in original['候选'] if r['Zone']==9*ZONE)
        require(target['有效']==21145689 and target['安全'] and not target['RR复制'],'关键候选改变')
        injected=[]
        for amount in (0,1,target['有效']//2,target['有效']):
            record=copy.deepcopy(original)
            changed=next(r for r in record['候选'] if r['Zone']==9*ZONE)
            changed['风险可知']=1;changed['RR复制']=amount
            replay=evaluate(binary,[record])[0]['新建议']
            injected.append(dict(诊断RR复制输入=amount,离线建议ZoneID=replay['Zone']//ZONE,
                全部方案含Zone9=all(9*ZONE in p['计划区'] for p in replay['计划']),方案=replay['计划']))
        require(all(r['离线建议ZoneID']==7 and r['全部方案含Zone9'] for r in injected),'敏感性结论与实际核不一致')
        evidence=next(e for e in proof['各臂'][tag]['候选随后RR详情'] if e['候选']['轮次']==12)
        rr=evidence['下一次维护'];rr_line=rr['行'];epoch=0;zc_same=[];finish=[];c2frames=[];rate=None
        for number,line in enumerate(lines,1):
            if '公共普通分配Finish代价：Zone='+str(9*ZONE)+'，' in line and '成功=1' in line and number<rr_line:
                finish.append(dict(行=number,时刻=line.split()[0]))
            if 'Zone 重置诊断：Zone='+str(9*ZONE)+'，状态=成功' in line:epoch+=1
            if '简单双因素实际集合：' in line and '实际选中=1' in line:
                r=fields(line,'简单双因素实际集合：')
                if int(r['Zone'])==9*ZONE and epoch==rr['本日志Reset序数']:zc_same.append(number)
            if '时序冻结块：轮次=12，' in line and 'Zone='+str(9*ZONE)+'，' in line:
                c2frames.append(dict(行=number,**fields(line,'时序冻结块：')))
            if '近期ETA对照：轮次=12，' in line and 'Zone='+str(9*ZONE)+'，' in line:
                rate=dict(行=number,**fields(line,'近期ETA对照：'))
        require(len(c2frames)==4 and {int(r['块']) for r in c2frames}=={34} and
            {int(r['RC']) for r in c2frames}=={976} and int(rate['十秒增量'])==0,'真实代表块记录不同')
        require(len({(r['代际'],r['PPA']) for r in c2frames})==1,'最大块身份变化，不能同块比较')
        copies=[];reset=None
        for number in range(rr_line,len(lines)):
            line=lines[number]
            if 'V1后续迁移复制：' in line:
                r=fields(line,'V1后续迁移复制：')
                if int(r['源Zone'])==9*ZONE and r['复制成功']=='1':copies.append(dict(行=number+1,**r))
            if 'Zone 重置诊断：Zone='+str(9*ZONE)+'，状态=成功' in line:
                reset=dict(行=number+1,时刻=line.split()[0]);break
        require(reset is not None and not zc_same and sum(int(r['有效字节']) for r in copies)==21145689,
            '该代际复制/Reset与单次RR账目不符')
        parse=lambda s:datetime.strptime(s,'%Y/%m/%d-%H:%M:%S.%f')
        finish_gap=(parse(evidence['候选']['时刻'])-parse(finish[-1]['时刻'])).total_seconds()
        results[tag]=dict(实际第12轮=original,实际评分重现=baseline['新建议'],敏感性诊断=injected,
            代表块C2四帧=c2frames,实际近期ETA对照=rate,最近成功Finish=finish[-1],Finish到候选秒=finish_gap,
            实际RR=evidence,同代际ZC行=zc_same,RR复制=copies,随后成功Reset=reset,
            原日志SHA256=digest(root/'run.zenfs.log'))
    output.mkdir(parents=True)
    result=dict(通过=True,来源=str(source),原始完整归档SHA256=proof['完整归档SHA256'],各臂=results,
        冻结评分核SHA256=digest(binary),冻结依赖=identity['头文件'],分析源码SHA256=digest(Path(__file__)),
        边界='敏感性诊断有意改RR成本输入；不是在线预测、不是新策略实际改选、不能声称减少RR或迁移。实际C4历史未完整记录。')
    (output/'真实证据与评分敏感性.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();r=audit(a.source,a.output)
    print(json.dumps({tag:dict(Finish到GC秒=x['Finish到候选秒'],诊断建议=[z['离线建议ZoneID'] for z in x['敏感性诊断']],
        实际该代际ZC次数=len(x['同代际ZC行']),实际RR复制字节=sum(int(z['有效字节']) for z in x['RR复制']))
        for tag,x in r['各臂'].items()},ensure_ascii=False,indent=2))
