#!/usr/bin/env python3
"""只读诊断旧长测替换前的局部成本，不模拟改变选择后的设备状态。"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from statistics import mean

from analyze_long_repeats import ROOT, ANCHOR, RESET, parse_ledger, file_size, stamp, require
from analyze_stream import MARKER, MIB
from analyze_maintenance_overlap import PREFIX
from replay_tail_guard import guard

RECEIPT = ROOT/"results/long-repeat-audit-20261015/重复迁移核验.json"
CAPACITY = 256*MIB
BUDGET = 192*MIB


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()


def numeric_fields(message, marker):
    return {k: int(v) for k,v in re.findall(r"([^，=]+)=(\d+)", message.split(marker,1)[1])}


def eligible(c):
    return (0 < c["有效字节"] <= c["容量字节"] and
            c["容量字节"]-c["有效字节"] >= BUDGET and
            c["覆盖资格"] == c["空目标资格"] == 1)


def local_fit(g, c, reserve):
    """输入仅为选择时快照，后续Finish、退役、重复复制不参与分类。"""
    if not g or not c or not reserve or not g["片段"] or not c["片段"]:
        return "信息不足"
    tail = int(reserve["剩余字节"])
    gv = sum(int(p["逻辑字节"]) for p in g["片段"])
    cv = sum(int(p["逻辑字节"]) for p in c["片段"])
    if gv <= tail < cv:
        return "Greedy全部可装而改选不能全部装入"
    if gv > tail and cv > tail:
        return "两者均不能全部装入"
    if cv <= tail:
        return "改选全部可装"
    return "信息不足"


def inspect(lines, ledger, origin, alignment):
    """按日志顺序恢复只含过去证据的布局，禁止用未来首次搬运反推源区。"""
    generations = defaultdict(int)
    created, writable, live, tails = {}, {}, {}, {}
    reserve = None
    candidates = []
    frames, by_start = [], {}
    copies = {c["结束行号"]: c for c in ledger["复制"]}
    batches = {b["行号"]: b for b in ledger["维护"] if b["类型"] == "ZC"}
    last_frame = None

    def pool_snapshot():
        if reserve is None or reserve not in tails:
            return None
        data = [v for v in live.values() if (v["Zone"],v["代际"]) == reserve]
        return {"可知":"1", "存在":"1", "Zone":str(reserve[0]), "代际":reserve[1],
                "剩余字节":str(tails[reserve]), "有效字节":str(sum(v["逻辑字节"] for v in data)),
                "历史依据行号":max([v["依据行号"] for v in data],default=0)}

    def candidate_snapshot(c, at):
        key = (c["Zone"], generations[c["Zone"]])
        parts = [dict(文件名=name, **v) for name,v in sorted(live.items())
                 if (v["Zone"],v["代际"]) == key and ANCHOR.fullmatch(name)]
        known = bool(parts) and sum(p["逻辑字节"] for p in parts) == c["有效字节"]
        require(all(p["依据行号"] < at for p in parts), "选择快照混入未来映射")
        return dict(Zone=c["Zone"], 代际=key[1], 有效字节=c["有效字节"],
                    最大RC=c["最大块读数"], 片段=[dict(p,次序=i) for i,p in enumerate(parts,1)] if known else [],
                    片段可知=known)

    for n,line in enumerate(lines,1):
        p = PREFIX.match(line)
        if not p:
            continue
        message, thread = p[3],p[2]
        m = re.search(r"New writable file: (\S+) direct: (\d+)", message)
        if m:
            writable[thread] = m[1]
            if ANCHOR.fullmatch(m[1]):
                require(m[1] not in created and m[2] == "1", "不是唯一直接写锚点")
                created[m[1]] = n
        m = re.search(r"Allocating zone\(new=[01]\) start: 0x([0-9a-f]+) wp: 0x([0-9a-f]+)", message)
        if m and ANCHOR.fullmatch(writable.get(thread,"")):
            name = writable[thread]
            zone,wp = (int(v,16) for v in m.groups())
            require(name not in live and zone == wp, "初始锚点不是唯一空区起点分配")
            live[name] = {"Zone":zone, "代际":generations[zone], "逻辑字节":file_size(name),
                          "依据行号":n, "创建行号":created[name], "此前复制次数":0}
        m = re.search(r"Rename file: (\S+) to : (\S+)", message)
        if m and m[1] in live:
            require(m[2] not in live, "改名覆盖历史文件")
            live[m[2]] = live.pop(m[1])
        m = re.search(r"DeleteFile: (\S+)\s*$",message)
        if m:
            live.pop(m[1],None)
        m = re.search(r"迁移预留取得空区：Zone=(\d+)",message)
        if m:
            zone = int(m[1])
            reserve = (zone,generations[zone])
            tails[reserve] = CAPACITY
        m = re.search(r"迁移预留替换：旧Zone=(\d+)，新Zone=(\d+)",message)
        if m:
            old,new = map(int,m.groups())
            require(reserve == (old,generations[old]), "替换的旧区不是当前同代预留")
            reserve = (new,generations[new])
            tails[reserve] = CAPACITY
        if "RR 封闭专用预留源：" in message:
            reserve = None
        if "简单双因素候选：" in message:
            candidates.append(numeric_fields(message,"简单双因素候选："))
        if "简单双因素实际集合：" in message:
            chosen = numeric_fields(message,"简单双因素实际集合：")
            allowed = [c for c in candidates if eligible(c)]
            if allowed:
                require(chosen["实际选中"] == 1, "有合格候选但未单区选择")
                g = min(allowed,key=lambda c:(c["有效字节"],c["Zone"]))
                selected = [c for c in allowed if c["Zone"] == chosen["Zone"]]
                require(len(selected) == 1, "实际候选不唯一")
                gs,cs = candidate_snapshot(g,n),candidate_snapshot(selected[0],n)
                pool = pool_snapshot()
                frame = dict(选择行号=n, 经过秒=stamp(p[1])-origin,
                             Greedy=gs, 实际候选=cs, 当前预留=pool,
                             相对Greedy改选=g["Zone"] != selected[0]["Zone"])
                frame["历史直接写载荷分类"] = local_fit(gs,cs,pool)
                frame["当前护栏物理上下界诊断"] = guard(gs,cs,pool or {},alignment)
                frames.append(frame)
                last_frame = frame
            else:
                last_frame = None
            candidates = []
        if n in batches:
            require(last_frame is not None and last_frame["实际候选"]["Zone"] == batches[n]["源Zone"],
                    "ZC批次缺少此前选择快照")
            by_start[n] = last_frame
        if n in copies:
            # 这里只使用到此行已经完成的复制，不读取该记录附带的未来退役和封闭标签。
            c = copies[n]
            old = live.get(c["文件名"])
            require(old is not None and (old["Zone"],old["代际"]) == (c["源Zone"],c["源代际"]) and
                    old["逻辑字节"] == c["字节"], "过去布局与实际复制源／长度不符")
            key = (c["目标Zone"],c["目标代际"])
            live[c["文件名"]] = dict(old, Zone=key[0], 代际=key[1], 依据行号=n,
                                      此前复制次数=old["此前复制次数"]+1)
            tails[key] = c["目标剩余字节"]
        m = RESET.search(message)
        if m:
            zone,before,after = map(int,m.groups())
            require(generations[zone] == before and after == before+1, "Reset链不连续")
            require(not any(v["Zone"] == zone and v["代际"] == before for v in live.values()),
                    "Reset源仍有已知有效文件，布局链不闭合")
            generations[zone] = after
            if reserve == (zone,before):
                reserve = (zone,after)
                tails[reserve] = CAPACITY

    result = []
    for closure in ledger["封闭"]:
        if closure["类型"] != "提前替换Finish":
            continue
        matches = [b for b in ledger["维护"] if b["行号"] < closure["行号"] < b["统计行号"]]
        require(len(matches) == 1, "替换缺少唯一维护上下文")
        batch = matches[0]
        frame = deepcopy(by_start.get(batch["行号"]))
        if frame is None:
            result.append(dict(替换=closure, 维护类型=batch["类型"], 分类="无ZC选区上下文"))
            continue
        before = frame["当前预留"]
        stable = (before is not None and int(before["Zone"]) == closure["Zone"] and
                  before["代际"] == closure["代际"] and int(before["剩余字节"]) == closure["尾部字节"])
        frame.update(替换=closure, 维护类型=batch["类型"], 选择尾部与替换尾部一致=stable,
                     分类=frame["历史直接写载荷分类"] if stable else "选择后状态改变或未知")
        # 以下全部为事后路径标签，附加前已完成选区快照分类。
        same = [c for c in ledger["复制"] if c.get("源封闭",{}).get("行号") == closure["行号"]]
        frame["事后同代源重复复制"] = [dict(文件名=c["文件名"], 类型=c["类型"], 字节=c["字节"],
            行号=c["行号"], 维护完成秒=c["维护完成秒"]) for c in same]
        result.append(frame)
    return dict(全部单区选择数=len(frames), 提前替换数=len(result),
                分类计数=dict(Counter(r["分类"] for r in result)),
                当前护栏分类计数=dict(Counter(r["当前护栏物理上下界诊断"]["分类"] for r in result
                                           if "当前护栏物理上下界诊断" in r)),
                事后路径汇总=dict(预热后发生替换数=sum(r["替换"]["经过秒"] >= 600 for r in result),
                    有后续同代重搬的旧目标数=sum(bool(r.get("事后同代源重复复制")) for r in result),
                    未观察到后续同代重搬的旧目标数=sum(not r.get("事后同代源重复复制") for r in result),
                    全程同代源重搬MiB=sum(c["字节"] for r in result for c in r.get("事后同代源重复复制",[]))/MIB,
                    计量区间同代源重搬MiB=sum(c["字节"] for r in result for c in r.get("事后同代源重复复制",[])
                                                   if 600 <= c["维护完成秒"] < 5400)/MIB),
                替换=result)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    # 复用上轮完整请求核验收据；本轮重新校验全部原始文件哈希及逐文件布局链。
    receipt = json.loads(RECEIPT.read_text())
    out = {"前次完整核验收据":str(RECEIPT), "收据SHA256":digest(RECEIPT),
           "限制":["只诊断历史同一快照的局部装入成本，不重放改变选择后的未来状态。",
                    "直接写布局使用选择前创建／分配／成功迁移／改名／删除证据；不从未来首次搬运反推源区。",
                    "当前护栏仍按未知稀疏头上下界计算，不能把直接写实况诊断冒充生产护栏已确定拦截。",
                    "其他普通目标对Greedy的匹配未知，保持护栏的保守成本判断。",
                    "后续重搬量不等于拦截后能省下的字节，也不补造旧日志缺失的近期同块读速率。"], "运行":{}}
    for name,pair in receipt["配对"].items():
        archived_source = ROOT/"results"/("stream-8020-build-20261013" if "8020" in name else
                                        "weighted-5050-20261012")/"build/fs_zenfs.cc"
        for mode in ("Greedy","加权"):
            old = pair[mode]
            run = Path(old["来源"])
            identity = {path:sha for sha,path in (row.split(maxsplit=1) for row in
                       (run/"identity.initial.sha256").read_text().splitlines())}
            require(digest(archived_source) == identity["plugin/zenfs/fs/fs_zenfs.cc"],
                    "历史文件排序／物理申请源码身份不符")
            for entry in (run/"logs.sha256").read_text().splitlines():
                sha,guest = entry.split(maxsplit=1)
                relative = Path(guest).relative_to(Path("/home/femu/rrzc-stream-results")/run.name)
                require(digest(run/relative) == sha, "原始文件哈希改变："+str(relative))
            require(digest(run/"zenfs.run.log") == old["日志SHA256"], "主日志与原核验收据不同")
            lines = (run/"zenfs.run.log").read_text().splitlines()
            origins = [stamp(p[1])-int(m[1])/1e9 for line in lines
                       if (p:=PREFIX.match(line)) and (m:=MARKER.search(line))]
            require(origins and max(origins)-min(origins) <= .020, "时钟不稳定")
            ledger = parse_ledger(lines,mean(origins))
            require(ledger == old["全程逐文件账"], "旧逐文件账与重新解析不一致")
            zones = json.loads((run/"zones.initial.json").read_text())["zone_list"]
            alignment,remainder = divmod(CAPACITY,int(zones[0]["cap"]))
            require(not remainder and alignment == 512, "旧设备单位不是已确认的512字节")
            out["运行"][name+"/"+mode] = dict(inspect(lines,ledger,mean(origins),alignment),
                原始文件哈希重新核验通过=True, 逐文件账重建一致=True,
                历史迁移源码=str(archived_source), 历史迁移源码SHA256=digest(archived_source))
    print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
