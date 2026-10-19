"""核验固定观察的真实时间覆盖；事后边界例外必须显式启用，不补采样。"""
import csv
from analyze_v1_shadow_stream import require, SECOND

PERIOD=SECOND//2


def check_timing(frames,seconds,final_ns,allow_final_overrun=False):
    """只接受末次查询越过下个周期、随后已结束业务的一次边界少采。"""
    expected=seconds*2
    require(frames and [r[0] for r in frames]==list(range(len(frames))),"观察批次不是完整连续序列")
    require(final_ns>=seconds*SECOND,"业务未到固定结束时点")
    require(all(0<=b<=e<=final_ns for _,b,e in frames),"查询时间超出有效观察范围")
    require(all(a[2]<=b[1] for a,b in zip(frames,frames[1:])),"查询时间倒退或相互重叠")
    # 默认计数检查不改变；只在指定900秒复核时另验严格的末端形状。
    if not allow_final_overrun:
        require(len(frames)==expected,"500ms完整32区轨迹缺失")
    else:
        require(seconds==900,"边界复核只限已注册900秒运行")
        require(len(frames) in (expected,expected-1),"不是只缺末端一个观察机会")
        require(all(i*PERIOD<=b<(i+1)*PERIOD for i,b,_ in frames),"中间采样离开所属周期，不能按末端少采处理")
        require(all(e<(i+1)*PERIOD for i,_,e in frames[:-1]),"中间查询已越过下周期，不能归为单次结束边界")
        if len(frames)==expected-1:
            i,begin,end=frames[-1]
            require(end-begin>PERIOD and end>=(i+1)*PERIOD and end<seconds*SECOND,
                    "末查询未超过采样周期，不能解释少采")
            require(end+PERIOD>final_ns,"末查询后本应仍有采样机会，不能豁免")
    return dict(实际批次=len(frames),理想批次=expected,原严格批数通过=len(frames)==expected,
        事后边界复核启用=allow_final_overrun,存在末端超时少采=len(frames)==expected-1,
        首查询开始秒=frames[0][1]/SECOND,末查询开始秒=frames[-1][1]/SECOND,
        末查询结束秒=frames[-1][2]/SECOND,末查询耗时毫秒=(frames[-1][2]-frames[-1][1])/1e6,
        最大查询开始间隔秒=max((b[1]-a[1] for a,b in zip(frames,frames[1:])),default=0)/SECOND,
        最终状态时点秒=final_ns/SECOND,末次超时后最早下次查询秒=(frames[-1][2]+PERIOD)/SECOND,
        补造或插值批次数=0,
        说明="固定500ms是目标节拍而非保证等间隔；事后边界复核不代表原预注册批数检查通过。")


def audit_sampling(run,samples,durations,seconds,allow_final_overrun=False):
    grouped={};ordered=[]
    keys=("查询开始纳秒","查询结束纳秒","设备会话高位","设备会话低位","RR阈值")
    with (run/"recent.csv").open() as stream:
        for raw in csv.DictReader(stream):
            batch=int(raw["批次"]);metadata=tuple(int(raw[k]) for k in keys)
            if batch not in grouped:
                require(batch==len(grouped),"CSV批次有跳号或顺序改变")
                grouped[batch]=(metadata,set());ordered.append((batch,*metadata[:2]))
            previous,zones=grouped[batch]
            require(batch==len(grouped)-1 and metadata==previous,"同批查询元数据不一致或批次交错")
            zone=int(raw["区索引"])
            require(zone not in zones,"CSV同批Zone重复")
            zones.add(zone)
    require(all(z==set(range(32)) for _,z in grouped.values()) and len(grouped)==len(durations),
            "批次不含完整32区或与逐块校验不符")
    with (run/"states.csv").open() as stream:
        states=list(csv.DictReader(stream))
    require(states,"缺少业务结束状态")
    result=check_timing(ordered,seconds,int(states[-1]["经过纳秒"]),allow_final_overrun)
    gaps=[b["time"]-a["time"] for records in samples.values() for a,b in zip(records,records[1:])]
    result["最大设备快照间隔秒"]=max(gaps,default=0)/SECOND
    if allow_final_overrun:
        # 沿用近期统计最多两秒的可用边界，不把实际空档补成500ms。
        require(gaps and min(gaps)>0 and max(gaps)<=2*SECOND,"同块观察时间覆盖有超过两秒空档")
    return result
