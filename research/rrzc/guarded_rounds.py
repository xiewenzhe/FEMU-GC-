"""独立重算新入口的同块短期准入、尾部与源/预留复核，拒绝篡改或遗漏。"""
from analyze_v1_shadow_stream import fields,require,predict,eta,MAX,SECOND,BUDGET,EXTRA
from analyze_v1_growth_stream import qualifies,empty_collection_count
from analyze_short_shadow_stream import short_eta,choose_short
from analyze_cost_observe import physical_bounds
from replay_tail_guard import guard
from tail_ablation_log import TailAblationLog
from cost_rank_log import CostRankLog


def source_check(row,pool_stable):
    if row["完整"]=="0":
        require(row["通过"]=="0","不完整源复核却通过"); return False
    require(row["完整"]=="1","源复核完整标志非法")
    stable=list(map(int,row["稳定可信"].split("/")))
    generation=list(map(int,row["主机代际"].split("/"))); sequence=list(map(int,row["主机序号"].split("/")))
    require(len(stable)==len(generation)==len(sequence)==3 and set(stable)<={0,1},"源复核窗口字段非法")
    passed=(pool_stable and row["统计准入"]=="1" and 0<=int(row["经过纳秒"])<=2*SECOND and
        row["当前写指针"]==row["原写指针"] and row["当前有效字节"]==row["原有效字节"] and
        all(stable) and len(set(generation))==len(set(sequence))==1)
    require(int(row["通过"])==int(passed),"源复核结果重算不符")
    return passed


def reserve_check(row):
    for k in ("启用","可知","存在","复核可知","复核存在","复核稳定"):
        require(row[k] in ("0","1"),"预留布尔字段非法")
    stable=(row["启用"]==row["可知"]==row["存在"]==row["复核可知"]==row["复核存在"]=="1" and
        all(row[k]==row["复核"+k] for k in ("Zone","剩余字节","有效字节","写指针","主机代际","主机序号")))
    require(row["复核稳定"]==str(int(stable)),"预留复核稳定结果重算不符")
    return stable


def parse(lines,strategy,tail_ablation=None,cost_rank=None,proposal_model=None,budget_for=None):
    ablation=TailAblationLog(tail_ablation,strategy)
    ranking=CostRankLog(cost_rank)
    pending=[];blocks={};gates={};parts={};summaries={};source=[];pool=None;old=None
    decisions=[];files=[];last=None;collections=0;maintenance=[]
    for number,line in enumerate(lines,1):
        # 新实验显式提供重算器；旧入口默认不调用，不改变旧对照的解释。
        if proposal_model is not None:proposal_model.observe(line)
        ablation.observe(line)
        ranking.observe(line)
        if "简单双因素候选：" in line:
            c=fields(line,"简单双因素候选：");v,cap=int(c["有效字节"]),int(c["容量字节"])
            require(0<v<=cap and cap==256<<20,"候选几何错误")
            risk=min(int(c["最大块读数"]),int(c["读数阈值"]))*1000000//int(c["读数阈值"]) if c["读压力可用"]=="1" else 0
            space=(cap-v)*1000000//cap
            require(tuple(int(c[k]) for k in ("空间分量ppm","风险分量ppm","等权分数ppm"))==(space,risk,(space+risk)//2),"候选分数不符")
            pending.append(c)
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：");z=int(b["Zone"])
            require(z not in blocks,"近期候选重复");blocks[z]=b
            if "当前RC" in b:
                rc,d10,d30,now,born,flags=(int(b[k]) for k in ("当前RC","十秒真实增量","三十秒真实增量","快照纳秒","代际起点纳秒","窗口标志"))
                require(0<=d10<=d30<=rc and born<=now and flags & 1 and not flags & ~15,"块窗口或读数矛盾")
                require(not flags & 2 or now-born>=10*SECOND,"十秒窗口不完整")
                require(not flags & 4 or now-born>=30*SECOND and flags & 2 and not flags & 8,"三十秒窗口不完整")
                ch,lun,plane,block=map(int,b["PPA"].split("/"))
                require(0<=ch<8 and 0<=lun<4 and 0<=plane<2 and block==z//(256<<20) and int(b["块编号"])==(ch*4+lun)*2+plane,"物理块身份错误")
                value=eta(b);require(int(b["预测可知"])==int(b["统计准入"]=="1" and value is not None) and int(b["预测达阈值纳秒"])==(MAX if value is None else value),"旧诊断预测不符")
        if "V1影子文件：" in line:files.append(dict(fields(line,"V1影子文件："),行号=number))
        if "V1增长候选：" in line:
            r=fields(line,"V1增长候选：");z=int(r["Zone"]);require(z not in gates,"增长记录重复");gates[z]=r
        if "V1增长决策：" in line:
            require(old is None,"旧诊断决策重复");old=fields(line,"V1增长决策：")
        if "独立约束片段：" in line:
            r=fields(line,"独立约束片段：");parts.setdefault(int(r["Zone"]),[]).append(r)
        if "独立约束候选：" in line:
            r=fields(line,"独立约束候选：");z=int(r["Zone"]);require(z not in summaries,"成本候选重复");summaries[z]=r
        if "独立约束源复核：" in line:source.append(fields(line,"独立约束源复核："))
        if "独立约束预留：" in line:
            require(pool is None,"预留记录重复");pool=fields(line,"独立约束预留：")
        if "独立约束决策：" in line:
            raw=fields(line,"独立约束决策：");rid=raw["轮次"]
            require(int(rid)==len(decisions)+1 and old and pool and old["轮次"]==pool["轮次"]==rid,"独立决策未完整关联")
            zones={int(c["Zone"]) for c in pending}
            require(len(zones)==len(pending) and zones==set(blocks)==set(gates)==set(summaries),"候选记录不完整")
            usable={z:b for z,b in blocks.items() if b.get("统计准入")=="1"};eligible=[c for c in pending if qualifies(c)]
            baseline=predict(eligible,usable);admitted={};estimates={};cost={}
            for c in pending:
                z=int(c["Zone"]);b=blocks[z];g=gates[z];s=summaries[z]
                require(all(r["轮次"]==rid for r in (b,g,s)),"候选轮次不符")
                if "当前RC" in b:require(int(b["原候选资格"])==qualifies(c),"原资格被修改")
                e=short_eta(usable.get(z),g);estimates[z]=e
                if z in usable and (z==baseline[0] or g["增长准入"]=="1"):admitted[z]=b
                require(int(s["短期预测可知"])==int(e is not None) and int(s["短期达阈值纳秒"])==(MAX if e is None else e) and s["有效字节"]==c["有效字节"],"短期估计不符")
                pp=parts.get(z,[]);low=high=covered=0;known=True;first=(0,0)
                for i,p in enumerate(pp,1):
                    require(p["轮次"]==rid and int(p["次序"])==i and int(p["容量字节"])==int(c["容量字节"]),"片段轮次、顺序或容量错误")
                    k,l,u=physical_bounds(int(p["逻辑字节"]),int(p["对齐字节"]));known=known and k and u<=int(c["容量字节"])
                    covered+=int(p["逻辑字节"]);low+=l;high+=u
                    if i==1:first=(l,u)
                require(tuple(int(s[k]) for k in ("物理界限可知","覆盖字节","片段数","首下界字节","首上界字节","总下界字节","总上界字节"))==(int(known),covered,len(pp),*first,low,high),"物理界限重算不符")
                cost[z]={"有效字节":c["有效字节"],"片段":[{"次序":p["次序"],"逻辑字节":p["逻辑字节"]} for p in pp]}
            require(tuple(int(old[k]) for k in ("原建议Zone","原会改选","原预测达阈值纳秒"))==(baseline[1] or 0,int(baseline[2]),baseline[4]),"旧预测诊断不符")
            admitted_prediction=predict(eligible,admitted)
            require(old["准入后建议Zone"]==str(admitted_prediction[1] or 0) and old["准入后会改选"]==str(int(admitted_prediction[2])),"旧增长诊断不符")
            greedy,suggest,would,extra,predicted,_=ranking.resolve(rid,pending,estimates,choose_short(pending,estimates))
            if proposal_model is not None:
                greedy,suggest,would,extra,predicted,_=proposal_model.resolve(
                    rid,pending,estimates,(greedy,suggest,would,extra,predicted,None))
            require(tuple(int(raw[k]) for k in ("存在Greedy","GreedyZone","短期建议Zone","原会改选","原额外有效字节","预测达阈值纳秒"))==(int(greedy is not None),greedy or 0,suggest or 0,int(would),extra,predicted),"新建议排序或预算不符")
            # 只有新实验显式传入已独立重算的预算；旧日志仍严格要求原16MiB。
            expected_budget=EXTRA if budget_for is None else budget_for(rid)
            require(raw["真实执行"]==str(int(strategy=="guarded")) and int(raw["界限纳秒"])==10*SECOND and int(raw["预算字节"])==expected_budget,"执行模式或参数改变")
            stable=reserve_check(pool)
            for r in source:require(r["轮次"]==rid,"源复核轮次不符")
            source_results=[source_check(r,stable) for r in source]
            if would:
                alignment=int(parts[greedy][0]["对齐字节"])
                require(all(int(p["对齐字节"])==alignment for z in (greedy,suggest) for p in parts[z]),"对齐参数不一致")
                evaluation=ablation.resolve(rid,would,guard(cost[greedy],cost[suggest],pool,alignment))
                if budget_for is not None:
                    # 尾部重算本身不变，但不能沿用旧报告中“额外预算不变”的文字。
                    evaluation.pop('原RR紧迫度及额外有效预算不变',None)
                    evaluation['原RR紧迫界限不变']=True
                    evaluation['额外有效预算来源']='本轮独立重算的收益预算'
                    evaluation['本轮额外有效预算字节']=expected_budget
                require(raw["尾部允许"]==str(int(evaluation["保留原建议"])) and raw["尾部分类"]==evaluation["分类"],"尾部约束不符")
                checked=bool(evaluation["保留原建议"] and stable and source_results==[True,True])
                if evaluation["保留原建议"] and stable:
                    expected_sources=[greedy,suggest] if checked else [greedy] if source_results==[False] else [greedy,suggest]
                    require([int(r["Zone"]) for r in source]==expected_sources,"源复核回调顺序或数量不符")
                    require(source_results in ([True,True],[False],[True,False]),"源复核结果遗漏")
                    for r in source:
                        if r["完整"]=="1":
                            original=next(c for c in pending if c["Zone"]==r["Zone"])
                            require(r["原有效字节"]==original["有效字节"] and
                                int(r["原写指针"])==int(original["Zone"])+int(original["容量字节"]),"源复核没有对应原满区候选")
                else:require(not source,"未获准仍执行了源复核")
            else:
                evaluation=ablation.resolve(rid,would,{"分类":"无需约束","保留原建议":False});checked=False
                require(raw["尾部允许"]=="0" and raw["尾部分类"]=="无需约束" and not source,"无原建议却通过成本准入")
            actual_switch=checked and strategy=="guarded"
            require(raw["复核后可改选"]==str(int(checked)) and raw["实际改选"]==str(int(actual_switch)) and int(raw["实际Zone"])==(suggest if actual_switch else greedy or 0) and int(raw["实际额外有效字节"])==(extra if actual_switch else 0),"真实选择或复核准入不符")
            d=dict(raw,行号=number,独立核验改选=actual_switch,
                   实际额外有效搬运字节=raw["实际额外有效字节"],成本核验=evaluation,
                   无Greedy预期集合数=empty_collection_count(pending));decisions.append(d);last=d
            pending=[];blocks={};gates={};parts={};summaries={};source=[];pool=None;old=None
        if "简单双因素实际集合：" in line:
            require(last is not None,"实际集合没有独立决策")
            c=fields(line,"简单双因素实际集合：")
            if last["存在Greedy"]=="1":
                require(c["实际选中"]=="1" and c["Zone"]==last["实际Zone"] and c["实际使用等权"]=="0","实际集合不符")
            else:require(int(c["实际选中"])==last["无Greedy预期集合数"],"等待或批量回退不符")
            last=None;collections+=1
        if "独立约束维护结果：" in line:maintenance.append(fields(line,"独立约束维护结果："))
    require(decisions and len(decisions)==collections and not pending and not blocks and not gates and
        not parts and not summaries and not source and pool is None and old is None and last is None,"新入口轮次未闭合")
    ablation.finish(len(decisions))
    ranking.finish(len(decisions))
    if proposal_model is not None:proposal_model.finish(len(decisions))
    chosen=[d for d in decisions if d["存在Greedy"]=="1"]
    require(len(chosen)==len(maintenance),"实际选区没有完整维护结果")
    for d,r in zip(chosen,maintenance):
        require(all(r[k]==d[k] for k in ("轮次","实际Zone","实际改选")) and r["选择时额外有效字节"]==d["实际额外有效字节"] and r["迁移成功"]=="1" and r["本轮重置区数"]=="1","维护结果与选择不符")
    return decisions,files,maintenance
