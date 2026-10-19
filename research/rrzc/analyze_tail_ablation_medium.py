#!/usr/bin/env python3
"""新900秒同库消融的完整账本比较；不以减少RR替代净成本评价。"""
import json
from pathlib import Path
import sys

from analyze_guarded_stream import analyze as base_analyze
from analyze_tail_ablation_smoke import expected_software, hashes
from analyze_v1_shadow_stream import require

LABEL = "tail-ablation-medium-20261015"
GUEST_LABEL = "guarded-medium-20261015-guarded"


def analyze(run, mode):
    require(mode in ("keep", "bypass") and run.name == GUEST_LABEL and
            run.parent.name == f"{LABEL}-{mode}", "不是本轮固定消融归档")
    expected = expected_software()
    require(hashes(run.parent/"完整源码身份.before.sha256") == expected, "不是已核验的新消融库")
    require((run.parent/"完整源码身份.before.sha256").read_bytes() ==
            (run.parent/"完整源码身份.after.sha256").read_bytes(), "业务期间完整源码或库改变")
    result = base_analyze(run, tail_ablation=mode)
    require(all(result["软件身份"].get(n) == d for n, d in expected.items()
                if not n.endswith("rrzc_tail_ablation.h")), "业务真实身份不符")
    result.update(消融模式=mode,完整核心身份=expected,
        说明="开跑前固定900秒原请求、180秒预热和真实采样规则；两组均执行，唯一处理差异为尾部准入。单次配对不能当作稳态或显著性证明。")
    return result


def compare(keep, bypass):
    require(keep["核验通过"] and bypass["核验通过"] and keep["消融模式"] == "keep" and
            bypass["消融模式"] == "bypass", "不是有效的保留／旁路配对")
    for key in ("配置", "负载配置", "软件身份", "完整核心身份", "每线程请求轨迹SHA256"):
        require(keep[key] == bypass[key], "两组条件不同："+key)
    a, b = keep["预热后计量"], bypass["预热后计量"]
    require((a["开始秒"], a["结束秒"]) == (b["开始秒"], b["结束秒"]) == (180, 900), "窗口不符")
    anchors = {name: dict(保留=keep["锚点维护账目"].get(name),旁路=bypass["锚点维护账目"].get(name),
        迁移变化MiB=(bypass["锚点维护账目"].get(name, {}).get("迁移MiB", 0)-
                    keep["锚点维护账目"].get(name, {}).get("迁移MiB", 0)))
        for name in sorted(set(keep["锚点维护账目"]) | set(bypass["锚点维护账目"]))}
    return dict(核验通过=True,保留尾部准入=keep,仅旁路尾部准入=bypass,
        **{"预热后净差（旁路减保留）": {k: b[k]-a[k] for k in
            ("ZC次数", "RR次数", "总迁移MiB", "首次迁移MiB", "重复迁移MiB", "维护源Reset", "全部IO区Reset")}},
        旁路全程迁移增加MiB=bypass["总维护迁移MiB"]-keep["总维护迁移MiB"],
        同名锚点维护对照=anchors,
        两组终态空间一致={k: v for k, v in keep["终态空间"].items() if k != "经过纳秒"} ==
            {k: v for k, v in bypass["终态空间"].items() if k != "经过纳秒"},
        两组逐块覆盖均达标=all(r["采样完整性核验"]["逐块轨迹覆盖达标"] for r in (keep, bypass)),
        说明="报告全部真实结果，包括零收益／负收益；覆盖不达标时预测后果保持未知，不插值、不自动重跑或调参。")


if __name__ == "__main__":
    require(len(sys.argv) == 3, "输入keep／bypass两个归档目录")
    print(json.dumps(compare(analyze(Path(sys.argv[1]), "keep"), analyze(Path(sys.argv[2]), "bypass")),
                     ensure_ascii=False, indent=2))
