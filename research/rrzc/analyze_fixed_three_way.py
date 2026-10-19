#!/usr/bin/env python3
"""同修复库三组逐文件成本对照；字节差额闭合，不将RR换名视为节省。"""
from collections import Counter
import json
from pathlib import Path
import sys

from analyze_guarded_stream import analyze as base_analyze
from analyze_tail_ablation_fixed import LABEL, GUEST_LABEL, expected_software, analyze as analyze_arm
from analyze_tail_ablation_smoke import hashes
from analyze_v1_shadow_stream import require

GREEDY_LABEL = LABEL+"-greedy"
GREEDY_GUEST = "guarded-medium-20261015-greedy"
RESULTS = Path(__file__).resolve().parent/"results"


def analyze_greedy(run):
    require(run.name==GREEDY_GUEST and run.parent.name==GREEDY_LABEL,"不是登记的同版本Greedy")
    expected = expected_software()
    require(hashes(run.parent/"完整源码身份.before.sha256")==expected,"Greedy非同一修复库")
    require((run.parent/"完整源码身份.before.sha256").read_bytes()==
            (run.parent/"完整源码身份.after.sha256").read_bytes(),"运行期间核心源／库改变")
    require((run.parent/"launcher.exit").read_text().strip()=="0","Greedy独立包装失败")
    result = base_analyze(run,fixed_greedy=True)
    outer_only = {"plugin/zenfs/fs/io_zenfs.cc","plugin/zenfs/fs/io_zenfs.h","plugin/zenfs/fs/rrzc_tail_ablation.h"}
    require(all(result["软件身份"].get(n)==d for n,d in expected.items() if n not in outer_only),
            "业务真实源码／库不符")
    require(result["实际改选次数"]==0,"Greedy发生真实改选")
    result["完整核心身份"] = expected
    return result


def file_comparison(greedy, treatment):
    """按跨运行同名锚点匹配，运行内仍按文件编号及Reset代际核验。"""
    output = []; categories = Counter()
    a,b = greedy["锚点维护账目"],treatment["锚点维护账目"]
    for name in sorted(set(a)|set(b)):
        old,new = a.get(name,{}),b.get(name,{})
        av,bv = old.get("迁移MiB",0),new.get("迁移MiB",0)
        at,bt = old.get("维护类型",{}),new.get("维护类型",{})
        if bv<av:
            category = "窗口内实际少复制"
        elif bv>av:
            category = "窗口内实际多复制"
        elif bt.get("RR",0)<at.get("RR",0) and bt.get("ZC",0)>at.get("ZC",0):
            category = "RR转为ZC但搬运不变"
        elif at!=bt:
            category = "搬运不变但维护构成不同"
        else:
            category = "搬运及维护次数相同"
        categories[category]+=1
        output.append(dict(文件=name,分类=category,Greedy迁移MiB=av,机制迁移MiB=bv,
            迁移变化MiB=bv-av,Greedy维护类型=at,机制维护类型=bt,
            Greedy复制=old.get("实际复制",[]),机制复制=new.get("实际复制",[])))
    delta = sum(r["迁移变化MiB"] for r in output)
    require(delta==treatment["总维护迁移MiB"]-greedy["总维护迁移MiB"],"逐文件差额未覆盖总账")
    return dict(统计范围="全程，包括准备及预热；不同文件的节省与增加分别列出",分类文件数=dict(categories),
        少复制MiB=-sum(min(0,r["迁移变化MiB"]) for r in output),
        多复制MiB=sum(max(0,r["迁移变化MiB"]) for r in output),净迁移变化MiB=delta,文件=output,
        说明="窗口内观测差异，不能仅凭一次少搬认定永久避免维护；非逐决策反事实归因。")


def compare_three(greedy, keep, bypass):
    require(all(r["核验通过"] for r in (greedy,keep,bypass)),"存在未通过核验的运行")
    require(greedy["策略"]=="greedy" and keep["消融模式"]=="keep" and bypass["消融模式"]=="bypass",
            "三组身份不符")
    output = {}
    for mode,treatment in (("保留尾部约束",keep),("关闭尾部约束",bypass)):
        for key in ("负载配置","软件身份","完整核心身份","每线程请求轨迹SHA256"):
            require(greedy[key]==treatment[key],"三组条件不同："+key)
        require({k:v for k,v in greedy["配置"].items() if k!="策略"}==
                {k:v for k,v in treatment["配置"].items() if k!="策略"},"固定配置不一致")
        a,b = greedy["预热后计量"],treatment["预热后计量"]
        require((a["开始秒"],a["结束秒"])==(b["开始秒"],b["结束秒"])==(180,900),"计量窗口不同")
        output[mode] = dict(逐文件成本=file_comparison(greedy,treatment),
            预热后净差={k:b[k]-a[k] for k in ("ZC次数","RR次数","总迁移MiB","首次迁移MiB",
                "重复迁移MiB","维护源Reset","全部IO区Reset")},
            终态空间差={k:int(treatment["终态空间"][k])-int(greedy["终态空间"][k]) for k in
                ("空闲字节","有效字节","满区可回收字节","空区","部分区","满区")})
    return dict(核验通过=True,Greedy=greedy,保留尾部约束=keep,关闭尾部约束=bypass,相对Greedy=output,
        三组逐块覆盖均达标=all(r["采样完整性核验"]["逐块轨迹覆盖达标"] for r in (greedy,keep,bypass)),
        说明="一次同软件固定请求三组对照；Greedy为补跑，非随机顺序或重复实验。完整记录净字节及终态空间，不按RR减少判成功。")


def analyze_all():
    return compare_three(analyze_greedy(RESULTS/GREEDY_LABEL/GREEDY_GUEST),
        analyze_arm(RESULTS/(LABEL+"-keep")/GUEST_LABEL,"keep"),
        analyze_arm(RESULTS/(LABEL+"-bypass")/GUEST_LABEL,"bypass"))


if __name__=="__main__":
    print(json.dumps(analyze_all(),ensure_ascii=False,indent=2))
