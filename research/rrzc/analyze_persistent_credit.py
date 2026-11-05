"""持续读修正版的真实受控配对验收；复用既有数据、安全及维护账目规则。"""
import argparse
import json
from pathlib import Path

from analyze_joint_opportunity import audit,compare,require,fields
from analyze_window_credit import audit_scores


def analyze_arm(root,mode,scenario):
    require(mode in ('greedy','window','persistent'),'未知持续读实验策略')
    root=Path(root);actual=audit(root,'direct',scenario)
    text=(root/'zenfs.run.log').read_text()
    scores=audit_scores(text.splitlines(),'window' if mode!='greedy' else 'greedy',persistent=mode=='persistent')
    enabled=int(mode!='greedy');persistent=int(mode=='persistent')
    require((root/'策略.txt').read_text().splitlines()==[
        '策略='+mode,'Greedy开关='+str(1-enabled),'窗口收益开关='+str(enabled),
        '稳定收益开关=0','RR公平队列=1','持续读开关='+str(persistent)],'实际执行策略与登记不符')
    require('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in text and
        text.count('RR公平队列服务：')==text.count('RR 开始：'),'共享RR队列或维护账目不符')
    actual.update(策略=mode,实际评分核验=scores)
    if scenario in ('burst-stopped','eligible-cost'):
        decisions=actual['实际模型成本及决策']
        require(decisions,'新受控场景没有实际评分')
        first=decisions[0];n=first['决策']['轮次'];sources={int(r['源Zone']) for r in actual['初态']}
        require({int(c['Zone']) for c in first['成本'] if c['安全']=='1'}==sources,
            '新场景要求两个原合格且安全的候选，不能用仅一个候选冒充成本权衡')
        hot=actual['初态'][1]['源Zone']
        blocks=[fields(s,'V1影子候选：') for s in text.splitlines() if 'V1影子候选：' in s]
        b=next(r for r in blocks if r['轮次']==n and r['Zone']==hot)
        rates=[fields(s.split('，原因=',1)[0],'近期ETA对照：') for s in text.splitlines() if '近期ETA对照：' in s]
        rate=next(r for r in rates if r['轮次']==n and r['Zone']==hot)
        require(int(b['十秒真实增量'])>0 and rate['近期可知']=='1','新场景缺旧热度或同块近期证据')
        if scenario=='burst-stopped':
            require(int(rate['近期增量'])==0 and int(b['当前RC'])==928,'未形成十秒有热度而短窗已停止')
        else:require(int(rate['近期增量'])>0,'成本权衡场景没有持续读风险')
        actual['区分场景核验']=dict(双合格安全候选=True,十秒增量=int(b['十秒真实增量']),
            近期增量=int(rate['近期增量']),近期纳秒=int(rate['间隔']))
    return actual


def analyze_pair(root,scenario,*,model='persistent'):
    require(model in ('window','persistent'),'配对模型未知')
    root=Path(root)
    arms=[analyze_arm(root/f'{scenario}-{mode}'/scenario,mode,scenario) for mode in ('greedy',model)]
    for mode in ('greedy',model):
        state=json.loads((root/f'{scenario}-{mode}'/'阶段归档.json').read_text())
        require(state['完整'] and not state['客机仍存活'],'归档或退出未完成')
    require((root/f'{scenario}-greedy'/scenario/'software.before.log').read_bytes()==
        (root/f'{scenario}-{model}'/scenario/'software.before.log').read_bytes(),'受控两臂软件不同')
    result=compare(*arms)
    result['模型版本']='窗口收益持续读修正版V1；短窗与原平均速率取较慢者，不改留存及直接排序' if model=='persistent' else '原窗口模型V1，未启用持续读修正'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--scenario',required=True,choices=('opportunity','stopped','low-yield','retire'))
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖已有结果')
    result=analyze_pair(args.root,args.scenario)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:result[k] for k in ('配对验收通过','场景','窗口内RR减少','窗口内复制减少字节')},ensure_ascii=False))


if __name__=='__main__':main()
