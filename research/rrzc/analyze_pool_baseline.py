"""协同池之前的新Open/Active基线：核验同配置两次Greedy，不评价模型收益。"""
import json
from pathlib import Path

from analyze_controlled_pair import reference_guard
from analyze_reproducibility import accounting
from analyze_calibration import timing
from cache_config_guard import check as cache_check


def analyze(output):
    output=Path(output)
    read=lambda p:json.loads(p.read_text())
    plan=read(output/'预注册计划.json');state=read(output/'状态.json')
    archive=read(output/'阶段归档.json')
    assert plan['仅Greedy新配置基线'] and not plan['模型配对']
    assert plan['实际执行组']==dict(g1='greedy',g2='greedy')
    assert archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
    assert state['当前客机PID'] is None
    limits=plan['本轮设备配置']['沿用条件']
    identify=read(output/'真实设备资源上限.json')
    assert identify['最大活动Zone数']==limits['最大活动Zone数']==12
    assert identify['最大打开Zone数']==limits['最大打开Zone数']==8
    source=output/state['标签']
    guard=reference_guard(source/'g1',source/'g2')
    metrics={}
    for tag in ('g1','g2'):
        root=source/tag
        cache_check((root/'run.rocksdb.log').read_text())
        assert (root/'wrapper.exit').read_text().strip()=='0'
        assert 'verifychecksum' in (root/'verify.log').read_text()
        metrics[tag]=dict(维护=accounting(root),计时=timing(root))
    result=dict(通过=guard['通过'],资源上限=dict(Open=8,Active=12),
        重复核验=guard,指标=metrics,
        边界='同配置完整受控Greedy重复；不跨旧0/6比较，不代表正常并发可重复性或协同池收益')
    (output/'新资源Greedy核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (output/'阶段一报告.md').write_text(
        '# 新资源配置Greedy基线\n\n'
        '真实设备Open=8、Active=12；独立重建起点，串行执行两次Greedy。\n\n'
        f'逐请求、起点、维护事件、迁移账目与设备摘要核验：{"通过" if guard["通过"] else "未通过"}。'
        '具体首差保留在`新资源Greedy核验.json`。\n\n'
        '本报告不作模型收益判断；只有核验通过才进入固定K=1原型。\n',encoding='utf-8')
    return result
