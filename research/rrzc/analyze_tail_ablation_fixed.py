#!/usr/bin/env python3
"""同一锁修复库的固定900秒尾部消融；旧结果不能混入本轮。"""
import json
from pathlib import Path
import sys

from analyze_guarded_stream import analyze as base_analyze
from analyze_tail_ablation_medium import compare
from analyze_tail_ablation_smoke import hashes
from analyze_v1_shadow_stream import require

LABEL = "tail-ablation-fixed-20261015"
GUEST_LABEL = "guarded-medium-20261015-guarded"
BUILD = Path(__file__).resolve().parent/"results/reopen-lock-build-20261015/rrzc-reopen-lock-build-20261015"
LIBRARY = "a8d44b61f75e12404ff58a195be54e3a60c44a9b69e0213881f50b38c9bf4072"
SOURCE = "0e37c61d06a7b1799049ce09ead85aa8485614cc2cb9a7474c9935e0476b7247"


def expected_software():
    expected = {"plugin/zenfs/"+n: d for n, d in hashes(BUILD/"source.after.sha256").items()}
    expected["librocksdb_debug.a"] = hashes(BUILD/"binaries.sha256")["librocksdb_debug.a"]
    require(expected["librocksdb_debug.a"] == LIBRARY and
            expected["plugin/zenfs/fs/fs_zenfs.cc"] == SOURCE, "不是已核验的锁修复构建")
    return expected


def analyze(run, mode):
    require(mode in ("keep", "bypass") and run.name == GUEST_LABEL and
            run.parent.name == f"{LABEL}-{mode}", "不是本轮修复版配对归档")
    expected = expected_software()
    require(hashes(run.parent/"完整源码身份.before.sha256") == expected, "修复版源／库身份不符")
    require((run.parent/"完整源码身份.before.sha256").read_bytes() ==
            (run.parent/"完整源码身份.after.sha256").read_bytes(), "运行期间源码或库改变")
    require((run.parent/"launcher.exit").read_text().strip() == "0", "独立业务包装未成功")
    result = base_analyze(run, tail_ablation=mode, reopen_fixed=True)
    actual = hashes(run/"identity.initial.sha256")
    # 原业务脚本未列这三项；由包装层的完整源身份前后核验覆盖。
    outer_only = {"plugin/zenfs/fs/io_zenfs.cc", "plugin/zenfs/fs/io_zenfs.h",
                  "plugin/zenfs/fs/rrzc_tail_ablation.h"}
    require(all(actual.get(n) == d for n, d in expected.items()
                if n not in outer_only), "业务实际核心身份不符")
    result.update(消融模式=mode,完整核心身份=expected,
        说明="同一锁修复库，固定900秒原请求、180秒预热；唯一选区差异为尾部准入。单次配对不证明稳态或显著性。")
    return result


if __name__ == "__main__":
    require(len(sys.argv) == 3, "输入本轮keep和bypass归档")
    print(json.dumps(compare(analyze(Path(sys.argv[1]), "keep"), analyze(Path(sys.argv[2]), "bypass")),
                     ensure_ascii=False, indent=2))
