#!/usr/bin/env python3
"""事后显式复核900秒采样末端超时；不改原状态，不重跑业务。"""
import argparse
import hashlib
import json
from pathlib import Path
from analyze_guarded_stream import analyze,compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-final-overrun",action="store_true",required=True,
                        help="明确接受独立、事后标注的末端查询超时复核，不代表原严格采样批数通过")
    parser.parse_args()
    root=Path(__file__).resolve().parent/"results"
    runs=[root/f"guarded-medium-20261015-{mode}"/f"guarded-medium-20261015-{mode}"
          for mode in ("greedy","guarded")]
    checked=[analyze(run,allow_final_overrun=True) for run in runs]
    result=compare(*checked)
    original=root/"guarded-medium-20261015/状态.json"
    result.update(结论性质="原严格采样批数未全部通过；本报告是独立事后边界复核，不是原监督器成功结果",
        原预注册采样批数条件全部通过=all(r["采样完整性核验"]["原严格批数通过"] for r in checked),
        原监督状态=json.loads(original.read_text()),原监督状态SHA256=hashlib.sha256(original.read_bytes()).hexdigest(),
        原始数据或策略修改=False,补采或业务重跑=False)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
