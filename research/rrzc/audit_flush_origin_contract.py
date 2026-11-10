"""复用已冻结寿命回放，核对Flush来源的证据边界；不训练预测器或驱动放置。"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGE = ROOT/'results/placement-ablation-20261105/阶段六'
BASE = Path('/home/data/xwz/rrzc-baselines/joint-controlled-placement-exit-fix-v2-20261106-results/源码')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(output):
    output = Path(output)
    if output.exists():
        raise ValueError('不覆盖已完成证据')
    history = STAGE/'第七步-历史寿命前缀回放'
    indexed = json.loads((history/'派生文件索引.json').read_text())
    inputs = [history/name for name in ('报告数据核验.json','全部Flush寿命及截尾.csv','原始证据索引.json')]
    for path in inputs:
        if sha(path) != indexed[str(path.relative_to(ROOT.parents[1]))]:
            raise ValueError('历史回放输入身份变化')
    verified = json.loads(inputs[0].read_text())
    with inputs[1].open(encoding='utf-8-sig',newline='') as stream:
        counts = Counter(row['模式'] for row in csv.DictReader(stream))
    if counts != {'G2':720, 'G2_WSURVIVOR':720}:
        raise ValueError('实际Flush文件数与已核验回放不符')
    visibility_path = STAGE/'第八步-LSM与备选可见性/可见性核验.json'
    visibility = json.loads(visibility_path.read_text())
    if not visibility['无未来快照']:
        raise ValueError('回放包含未来快照')
    source = BASE/'协同池副本/plugin/zenfs/fs'
    anchors = [
        (source/'rrzc_flush_origin.h','struct Context { bool active=false; uint64_t job=0; };','来源上下文只有Flush执行标志和任务号'),
        (source/'io_zenfs.h','bool flush_origin_w_=false;','旧W是来源字段'),
        (source/'io_zenfs.h','if(rrzc_stream::Enabled())return (flush_origin_w_?2:0)','来源与H拼为旧位掩码，未提供寿命预测'),
        (source/'io_zenfs.cc','void ZoneFile::MaybeCaptureFlushOrigin()','首次分配前捕获来源，不携带Key更新率'),
        (source/'io_zenfs.cc','IOStatus ZoneFile::SetWriteLifeTimeHint(','保存上游寿命提示，枚举不能当真实剩余寿命'),
        (source/'rrzc_colocation_pool.h','item.second.observed_tags|static_cast<uint8_t>(tag)','互补排序使用历史标签，而非预测失效量'),
    ]
    evidence = []
    for path, anchor, meaning in anchors:
        lines = path.read_text().splitlines()
        found = [i+1 for i, line in enumerate(lines) if anchor in line]
        if len(found) != 1:
            raise ValueError('源码锚点不唯一：'+anchor)
        evidence.append(dict(路径=str(path),行号=found[0],锚点=anchor,含义=meaning,SHA256=sha(path)))
    features = [
        dict(信息='Flush来源F',决策前证据='真实Scope/任务号',能否说明短寿命='不能',本轮用途='保留来源事实及来源集中对照'),
        dict(信息='当前H',决策前证据='原Profile滑动事件窗口',能否说明短寿命='不能',本轮用途='保留原读热状态；不是物理RC'),
        dict(信息='文件年龄',决策前证据='已有出生后可计算；新文件年龄近零',能否说明短寿命='已有年轻长寿命反例',本轮用途='仅诊断，不新增年龄门槛'),
        dict(信息='历史同类寿命',决策前证据='已完成回放严格截断未来',能否说明短寿命='群体多数不能可靠识别昂贵例外',本轮用途='不直接授予混放资格或Score'),
        dict(信息='CF/创建Level',决策前证据='上层BuildTable可见，现有Flush Scope未传递',能否说明短寿命='本轮样本同为default/L0，不能区分',本轮用途='不为增加字段而接入'),
        dict(信息='文件当前Level/Key区间',决策前证据='已存在文件的过去布局快照，可能陈旧；新Flush不可见',能否说明短寿命='区间相交不是覆盖更新比例',本轮用途='未知保持未知，不在ZenFS锁内回调DB'),
        dict(信息='WriteLifeTimeHint',决策前证据='ZenFS已有枚举提示',能否说明短寿命='当前未校准其对RR前退役的预测能力',本轮用途='保持普通分配原用途，不升级为短寿命证明'),
        dict(信息='已提交迁移历史',决策前证据='现有候选仅本挂载会话可得',能否说明短寿命='只能证明此前维护时仍有效',本轮用途='不能保证首次混放合适'),
    ]
    output.mkdir(parents=True)
    with (output/'决策前信息与寿命证据.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(features[0]))
        writer.writeheader();writer.writerows(features)
    result = dict(核验通过=True,新业务实验=False,新预测器=False,历史回放复用=True,
        已核验Flush文件数=dict(counts),既有反例=verified,
        新Flush前置LSM可见={mode:data['按路径']['新Flush进入H区'] for mode,data in visibility['各臂'].items()},
        源码证据=evidence,输入身份={str(p):sha(p) for p in inputs+[visibility_path,history/'派生文件索引.json']},
        结论='F只表达来源，寿命未知；不从来源、H或群体寿命直接授予短寿命资格；现有G2混放行为并未因本接口被关闭',
        推荐现成参照='G1：来源集中，必要迁移H/HF不主动入池；自然变H与后续追加仍可共存，不是零混放')
    (output/'证据核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    result=audit(args.output)
    print(json.dumps({k:result[k] for k in ('核验通过','新业务实验','新预测器','已核验Flush文件数')},ensure_ascii=False))
