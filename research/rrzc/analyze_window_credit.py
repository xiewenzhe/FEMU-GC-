"""窗口模型真实短对照核验；按实际发布序号重建输入，不以重放代替设备运行。"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path

from analyze_joint_opportunity import audit, compare, fields, require, model_decisions
from analyze_recent_eta import validate_rate
from replay_window_credit import oracle, retention, MAX, SECOND


def exact_history(lines):
    frames={};current={};published={};frozen={}
    for line in lines:
        if '留存原始采样：' in line:
            p=fields(line,'留存原始采样：');z=int(p['Zone'])
            if p['阶段']=='采样':
                key=p['会话'],p['迁移后纳秒'];frame=frames.setdefault(key,{})
                require(z not in frame,'同一原始帧同区重复');frame[z]=p
            else:
                key=int(p['轮次']),z;require(key not in current,'选区点重复');current[key]=p
        elif '窗口收益历史发布：' in line:
            r=fields(line,'窗口收益历史发布：');n=int(r['发布序号'])
            require(n>0 and n not in published,'发布序号无效或重复');published[n]=r
        elif '窗口收益历史冻结：' in line:
            r=fields(line,'窗口收益历史冻结：');n=int(r['轮次'])
            require(n not in frozen,'同轮重复冻结');frozen[n]=int(r['发布序号'])
    require(sorted(published)==list(range(1,len(published)+1)),'发布历史不完整')
    snapshots={0:[]};history=deque(maxlen=40);session=None;after=0
    wanted=set(frozen.values())
    for n,r in sorted(published.items()):
        key=r['会话'],r['迁移后纳秒']
        frame=frames.get(key,{})
        if r['可用']!='1' or not frame:history.clear()
        else:
            if session!=key[0] or after>=int(key[1]):history.clear()
            history.append(frame);session=key[0];after=int(key[1])
        if n in wanted:snapshots[n]=list(history)
    require(all(n in snapshots for n in wanted),'冻结引用不存在的发布序号')
    return {n:snapshots[seq] for n,seq in frozen.items()},current,frozen


def audit_scores(lines,mode,*,persistent=False,expected_rr=False,candidate_pool='original',space_plan=False,deadline=False,joint_time=False,terminal=False,terminal_version=1):
    require(not any('支持预测配置：' in line and '，启用=1，' in line for line in lines),
            '旧评分入口拒绝支持预测新核，须使用独立版本核验')
    require(terminal or not any('边界配置：' in line and '，启用=1，' in line for line in lines),'旧评分入口拒绝边界新核')
    require(not terminal or (deadline and joint_time),'边界新核须显式验收时间一致预测')
    require(deadline or not any('时序方案决策：' in line for line in lines),'旧评分入口拒绝时序实验')
    require(space_plan or not any('空间方案决策：' in line for line in lines),
            '旧评分入口拒绝新空间方案；必须显式核验方案总成本和实际决策')
    require(mode in ('greedy','window'),'未知策略')
    require(candidate_pool in ('original','expanded'),'未知候选范围')
    pool=[fields(line,'候选范围配置：') for line in lines if '候选范围配置：' in line]
    if pool or candidate_pool=='expanded':
        require(len(pool)==1 and pool[0]['合法']=='1' and
                pool[0]['扩大']==str(int(candidate_pool=='expanded')) and
                all(pool[0][k]=='1' for k in ('版本','GC启动不变','满区要求不变',
                    '正回收要求不变','公共安全不变','评分公式不变')),'候选范围执行配置不符')
    require(not expected_rr or (persistent and mode=='window'),'期望RR验收必须显式选择持续读窗口分支')
    tags=('V1影子候选','近期ETA对照','统一成本','窗口收益成本','窗口收益模式','稳定收益配对模式','持续读收益成本','期望RR成本')
    maps={tag:{} for tag in tags};config=[];persistent_config=[];expected_config=[]
    for line in lines:
        require(expected_rr or ('期望RR配置：' not in line and '期望RR成本：' not in line),
            '旧窗口/持续读核验入口拒绝混入期望RR实验模型')
        if '窗口收益配置：' in line:config.append(fields(line,'窗口收益配置：'))
        if '持续读收益配置：' in line:persistent_config.append(fields(line,'持续读收益配置：'))
        if '期望RR配置：' in line:expected_config.append(fields(line,'期望RR配置：'))
        require('稳定收益模式：' not in line and '稳定收益成本：' not in line,'混入旧稳定模型执行')
        for tag in tags:
            if tag+'：' in line:
                r=fields(line.split('，原因=',1)[0],tag+'：')
                key=(int(r['轮次']),int(r['Zone'])) if 'Zone' in r else int(r['轮次'])
                require(key not in maps[tag],'同轮日志重复：'+tag);maps[tag][key]=r
    require(maps['统一成本'] and maps['稳定收益配对模式'],'缺真实评分或实际模式')
    if persistent:
        require(mode=='window' and len(persistent_config)==1,'持续读执行配置缺失或模式冲突')
        p=persistent_config[0]
        require(p['请求']==p['启用']=='1' and p['版本']=='1' and
            int(p['短间隔最小'])==SECOND//2 and int(p['短间隔最大'])==2*SECOND and
            p['原窗口留存不变']=='1' and p['先选Greedy']=='0','持续读执行参数不符')
        require(set(maps['持续读收益成本'])==set(maps['窗口收益成本']),'持续读诊断集合与实际窗口诊断不符')
    else:
        require(not persistent_config and not maps['持续读收益成本'],'旧核验入口拒绝混入持续读修正版')
    if expected_rr:
        require(len(expected_config)==1,'期望RR实际配置缺失或重复')
        p=expected_config[0]
        require(p['请求']==p['启用']=='1' and p['版本']=='1' and p['概率尺度']=='1000000000' and
            p['先选Greedy']=='0' and p['泊松假设未校准']=='1','期望RR执行配置不符')
        require(set(maps['期望RR成本'])==set(maps['窗口收益成本']),'期望RR评分诊断集合不完整')
    def estimate(values,key):
        if not persistent:return oracle(values)
        # 新分支显式选择独立整数公式；旧入口仍使用原公式，不能默默重解释历史结果。
        from replay_persistent_credit import oracle as persistent_oracle
        rate=maps['近期ETA对照'].get(key)
        growth=(int(rate['近期可知']),int(rate.get('近期增量',0)),int(rate.get('间隔',0))) if rate else (0,0,0)
        out=persistent_oracle((*values,*growth));p=maps['持续读收益成本'][key]
        require(p['版本']=='1' and tuple(int(p[k]) for k in ('平均ETA','近期ETA','原抵扣'))==out[8:11],
            '持续读原始及近期ETA或原抵扣不一致')
        # 已达阈值时近期ETA观察器提前返回，不调用Check；持续读分支仍调用Check。
        # 二者未使用的增长诊断可以不同，但当前RC、阈值及所有评分仍须精确核验。
        due=expected_rr and values[8]>0 and values[3]>=values[8] and out[0]
        if due:
            known,delta,span=(int(p[k]) for k in ('近期可知','近期增量','近期间隔'))
            require(known in (0,1) and 0<=delta<=values[3] and 0<=span<=MAX and
                (not known or SECOND//2<=span<=2*SECOND and span<=values[7]),
                '已达阈值的附带增长诊断不合法；不将其解释为预测证据')
        else:
            require(tuple(int(p[k]) for k in ('近期可知','近期增量','近期间隔'))==growth,
                '持续读证据或修正评分不一致')
        require(int(p['修正ETA'])==out[1] and int(p['修正抵扣'])==out[3],'持续读证据或修正评分不一致')
        if not expected_rr:return out[:8]
        # 数值概率独立用十进制CDF重算；只允许定点最后一单位的算法量化差。
        from replay_expected_rr import oracle as expected_oracle,SCALE
        predicted=expected_oracle((*values,*growth));e=maps['期望RR成本'][key]
        units=int(e['需求概率十亿分数']);credit=predicted[2]*units//SCALE
        tolerance=1 if predicted[7] and values[3]<values[8] else 0
        ceiling=SCALE-1 if values[3]<values[8] else SCALE
        require(e['版本']=='1' and e['概率可计算']==str(predicted[7]) and
            0<=units<=ceiling and abs(units-predicted[8])<=tolerance,'需求概率与独立CDF核验不符')
        require(tuple(int(e[k]) for k in ('ETA','预计留存','原持续读抵扣'))==
            (predicted[1],predicted[2],predicted[9]),'期望RR基础证据不一致')
        require(int(e['新抵扣'])==credit and int(e['净复制'])==values[1]-credit,
            '期望RR概率定点化与字节账目不一致')
        return predicted[:3]+(credit,values[1]-credit,predicted[5],out[6],out[7])
    history,current,frozen=exact_history(lines)
    if mode=='window':
        require(len(config)==1 and config[0]['请求']==config[0]['启用']=='1' and
            config[0]['版本']=='1' and int(config[0]['最大支撑窗口'])==10*SECOND and
            config[0]['先选Greedy']=='0' and
            config[0]['原候选资格不变']==str(int(candidate_pool=='original')),'窗口模型未实际启用')
    else:
        require(not config and not frozen and not maps['窗口收益模式'] and
            not maps['窗口收益成本'],'Greedy启用了窗口模型')
    rounds=set(maps['稳定收益配对模式']);result=[]
    for n,r in maps['稳定收益配对模式'].items():
        require(r['Greedy']==str(int(mode=='greedy')) and r['稳定模型']=='0' and
            r['共用安全复核']=='1','实际执行分支冲突')
        if mode=='window':
            actual=maps['窗口收益模式'].get(n)
            require(actual is not None and actual['执行']=='1' and actual['版本']=='1' and
                int(actual['时间尺度'])==10*SECOND and actual['先选Greedy']=='0' and
                int(actual['历史序号'])==frozen.get(n),'执行未对应同轮冻结历史')
    for key,cost in maps['统一成本'].items():
        n,z=key;require(n in rounds,'缺实际模式')
        b=maps['V1影子候选'].get(key);require(b is not None and b['原候选资格']=='1','缺原候选资格')
        v=int(cost['有效']);reclaim=int(cost['回收']);f=[0]*8
        if mode=='window' and b['统计准入']=='1':
            p=current.get(key)
            if p is not None and int(p['有效字节'])==v:f=retention(history[n],p)
        if b['统计准入']=='1':
            r=maps['近期ETA对照'].get(key);require(r is not None,'缺设备同块身份核验')
            validate_rate(r,b);require(int(r['阈值'])==1024,'RR阈值变化')
            values=(z,v,v+reclaim,int(b['当前RC']),int(b['十秒真实增量']),int(b['三十秒真实增量']),
                int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),1024,int(b['设备代际']),*f)
        else:
            require(key not in maps['近期ETA对照'],'未知统计却有准入诊断')
            values=(z,v,v+reclaim,0,0,0,0,0,0,0,*f)
        expected=estimate(values,key) if mode=='window' else (0,MAX,0,0,v,reclaim,0,0)
        require(tuple(int(cost[k]) for k in ('风险可知','ETA','预计留存','RR抵扣','净复制','回收'))==expected[:6],
            '执行评分与独立整数重算不一致')
        if mode=='window':
            logged=maps['窗口收益成本'].get(key);require(logged is not None,'缺实际窗口评分诊断')
            require(tuple(int(logged[k]) for k in ('留存可知','留存Zone','留存代际','留存有效',
                '短减少','短间隔','长减少','长间隔'))==tuple(f),'留存估计与真实冻结序号原始帧不符')
            require(tuple(int(logged[k]) for k in ('风险可知','ETA','预计留存','RR抵扣','净复制','读间隔','支撑窗口'))
                ==expected[:5]+expected[6:],'实际评分诊断与独立重算不一致')
            require(int(cost['留存可知'])==f[0],'统一成本误用了旧留存可知')
        result.append(dict(轮次=n,Zone=z,ETA=expected[1],RR抵扣=expected[3],净复制=expected[4]))
    if mode=='window':
        eligible={k for k,b in maps['V1影子候选'].items() if b['原候选资格']=='1'}
        require(set(maps['统一成本'])==eligible,'原合格候选缺少实际成本或不合格区参与排序')
        require(set(maps['统一成本'])<=set(maps['窗口收益成本']),'合格候选缺少评分诊断')
        # 接线先输出初步候选诊断，Rank随后过滤资格；不能把诊断存在当成参与排序。
        # 额外记录仍逐条验证，既不静默丢弃，也不能容许合格候选成本丢失。
        for key in set(maps['窗口收益成本'])-set(maps['统一成本']):
            n,z=key;b=maps['V1影子候选'].get(key);logged=maps['窗口收益成本'][key]
            require(n in rounds and b is not None and b['原候选资格']=='0',
                '额外诊断未对应同轮明确不合格的初步候选')
            f=[0]*8;v=int(b['有效字节'])
            if b['统计准入']=='1':
                p=current.get(key);r=maps['近期ETA对照'].get(key)
                require(p is not None and r is not None,'不合格区诊断缺原始观察')
                validate_rate(r,b);require(int(r['阈值'])==1024,'RR阈值变化')
                if int(p['有效字节'])==v:f=retention(history[n],p)
                values=(z,v,128<<20,int(b['当前RC']),int(b['十秒真实增量']),int(b['三十秒真实增量']),
                    int(b['窗口标志']),int(b['快照纳秒'])-int(b['代际起点纳秒']),1024,int(b['设备代际']),*f)
            else:
                require(key not in maps['近期ETA对照'],'未知统计却有准入诊断')
                values=(z,v,128<<20,0,0,0,0,0,0,0,*f)
            expected=estimate(values,key)
            require(tuple(int(logged[k]) for k in ('留存可知','留存Zone','留存代际','留存有效',
                '短减少','短间隔','长减少','长间隔'))==tuple(f),'额外诊断留存与冻结历史不符')
            require(tuple(int(logged[k]) for k in ('风险可知','ETA','预计留存','RR抵扣','净复制','读间隔','支撑窗口'))
                ==expected[:5]+expected[6:],'额外诊断评分重算不一致')
    else:require(not maps['窗口收益成本'],'Greedy混入新模型诊断')
    if deadline:
        from analyze_deadline import audit,audit_forecasts
        if terminal:
            from analyze_terminal import audit
            audit(lines,version=terminal_version)
        else:audit(lines)
        audit_forecasts(lines,time_consistent=joint_time)
    elif space_plan:
        from analyze_space_plan import audit_plans
        audit_plans(lines)
    else:model_decisions(lines)
    return result


def analyze_arm(root,mode,scenario):
    actual=audit(root,'direct',scenario);raw=root/'zenfs.run.log';lines=raw.read_text().splitlines()
    scores=audit_scores(lines,mode)
    require((root/'策略.txt').read_text().splitlines()==[
        '策略='+mode,'Greedy开关='+str(int(mode=='greedy')),'窗口收益开关='+str(int(mode=='window')),
        '稳定收益开关=0','RR公平队列=1'],'包装策略登记不符')
    text='\n'.join(lines)
    require('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in text and
        text.count('RR公平队列服务：')==text.count('RR 开始：'),'两臂共享RR修复未实际生效')
    actual.update(策略=mode,实际评分核验=scores,日志SHA256=hashlib.sha256(raw.read_bytes()).hexdigest())
    return actual


def analyze_pair(root,scenario):
    arms=[analyze_arm(root/f'{scenario}-{mode}'/scenario,mode,scenario) for mode in ('greedy','window')]
    for mode in ('greedy','window'):
        archive=json.loads((root/f'{scenario}-{mode}'/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'],'归档或退出核验未通过')
    require((root/f'{scenario}-greedy'/scenario/'software.before.log').read_bytes()==
        (root/f'{scenario}-window'/scenario/'software.before.log').read_bytes(),'两臂库或程序身份不同')
    result=compare(*arms);result['模型版本']='窗口自适应净维护成本V1，按实际发布序号重建历史'
    result['原始证据身份']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for mode in ('greedy','window') for p in (root/f'{scenario}-{mode}'/scenario).rglob('*') if p.is_file()}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--scenario',required=True,choices=('opportunity','stopped','low-yield','retire'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    require(not args.output.exists(),'拒绝覆盖既有核验')
    result=analyze_pair(args.root,args.scenario)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:result[k] for k in ('场景','配对验收通过','窗口内RR减少','窗口内复制减少字节')},ensure_ascii=False))


if __name__=='__main__':main()
