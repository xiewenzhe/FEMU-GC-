"""新稳定收益模型真实受控配对验收；逐行重算，不预设正收益。"""
import argparse
import hashlib
import json
from pathlib import Path

from analyze_joint_opportunity import audit, compare, fields, require
from analyze_recent_eta import validate_rate
from replay_stable_credit import oracle, MAX


def audit_scores(lines,mode):
    require(mode in ('greedy','stable'),'未知配对策略')
    maps={tag:{} for tag in ('V1影子候选','近期ETA对照','统一留存','统一成本','稳定收益成本')}
    modes=[]
    for line in lines:
        if '稳定收益配对模式：' in line:
            r=fields(line,'稳定收益配对模式：');modes.append(r)
            require(r['Greedy']==str(int(mode=='greedy')) and r['稳定模型']==str(int(mode=='stable')) and
                    r['共用安全复核']=='1','实际执行模式或共用安全路径不符')
        for tag,index in maps.items():
            if tag+'：' in line:
                # 中文原因可能含逗号，不能按字段分隔符拆成伪键值。
                row=fields(line.split('，原因=',1)[0],tag+'：');key=(row['轮次'],row['Zone'])
                require(key not in index,'同轮同区日志重复')
                index[key]=row
    require(modes,'缺实际执行模式，不仅依赖环境变量声明')
    seen={r['轮次'] for r in modes};result=[]
    for key,cost in maps['统一成本'].items():
        require(key[0] in seen,'成本缺对应执行模式')
        b=maps['V1影子候选'].get(key);f=maps['统一留存'].get(key);r=maps['近期ETA对照'].get(key)
        require(b is not None and f is not None and b['原候选资格']=='1','缺候选资格或留存证据')
        valid,reclaim=int(cost['有效']),int(cost['回收'])
        if b['统计准入']=='1':
            require(r is not None,'缺同块近期证据');validate_rate(r,b)
            require(r['新策略']=='1','近期对照开关缺失')
            values=(int(cost['Zone']),valid,valid+reclaim,int(b['当前RC']),int(b['十秒真实增量']),
                int(b['三十秒真实增量']),int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),
                int(r['阈值']),int(r['近期可知']),int(r['近期增量']),int(r['间隔']))
        else:
            require(r is None,'未知准入与近期证据矛盾')
            values=(int(cost['Zone']),valid,valid+reclaim,0,0,0,0,0,0,0,0,0)
        values+=tuple(int(f[n]) for n in ('可知','十秒减少','三十秒减少','十秒间隔','三十秒间隔'))
        expected=oracle(values) if mode=='stable' else (0,MAX,0,0,valid,reclaim,MAX,MAX)
        actual=tuple(int(cost[n]) for n in ('风险可知','ETA','预计留存','RR抵扣','净复制','回收'))
        require(actual==expected[:6],'实际执行评分与独立重算不一致')
        if mode=='stable':
            logged=maps['稳定收益成本'].get(key);require(logged is not None,'缺稳定模型诊断')
            require(tuple(int(logged[n]) for n in ('风险可知','稳定ETA','预计留存','RR抵扣','净复制',
                                                  '短窗ETA','十秒ETA'))==expected[:5]+expected[6:],
                    '稳定模型诊断与实际评分不一致')
        result.append(dict(轮次=int(key[0]),Zone=int(key[1]),RR抵扣=actual[3],ETA=actual[1],净复制=actual[4]))
    require(result,'没有实际评分候选')
    if mode=='greedy':require(not maps['稳定收益成本'],'Greedy实际启用了新模型')
    return result


def analyze_arm(root,mode,scenario):
    # 两臂共用Direct安全执行入口；Greedy只是零抵扣评分，非原stock批量GC。
    actual=audit(root,'direct',scenario)
    raw=root/'zenfs.run.log'
    scores=audit_scores(raw.read_text().splitlines(),mode)
    require((root/'策略.txt').read_text().splitlines()==[
        '策略='+mode,'Greedy开关='+str(int(mode=='greedy')),'稳定收益开关='+str(int(mode=='stable'))],
        '包装策略登记不符')
    actual.update(策略=mode,实际评分核验=scores,日志SHA256=hashlib.sha256(raw.read_bytes()).hexdigest())
    return actual


def analyze_pair(root,scenario):
    arms=[analyze_arm(root/f'{scenario}-{mode}'/scenario,mode,scenario) for mode in ('greedy','stable')]
    for mode in ('greedy','stable'):
        require(json.loads((root/f'{scenario}-{mode}'/'阶段归档.json').read_text())['完整'], '归档或关机未通过')
    require((root/f'{scenario}-greedy'/scenario/'software.before.log').read_bytes()==
            (root/f'{scenario}-stable'/scenario/'software.before.log').read_bytes(),'两臂实际库不同')
    result=compare(*arms)
    result['模型版本']='稳定同块速率及H/(H+ETA)折减，H=10秒'
    result['原始证据身份']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for mode in ('greedy','stable') for p in sorted((root/f'{scenario}-{mode}'/scenario).rglob('*')) if p.is_file()}
    result['边界']='真实受控25秒窗口，不是YCSB；不预设正收益，不能证明永久避免RR或稳态成本下降'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--scenario',required=True,choices=('opportunity','retire','stopped','low-yield'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖已有核验')
    result=analyze_pair(args.root,args.scenario)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:result[k] for k in ('场景','配对验收通过','窗口内RR减少','窗口内复制减少字节')},ensure_ascii=False))


if __name__=='__main__':main()
