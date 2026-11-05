"""装载期只保留预注册H；W只能来自随后真实业务Flush，不能伪称已混合。"""
import json
from pathlib import Path
import sys
from pool_dbbench_guest import table, roles


def validate_source(target):
    planned=json.loads((target/'受控标签来源.json').read_text())
    assert planned['标签范围']=='业务Flush'
    rows=table(target/'source/装载-0.sst.csv')
    expected={n:t for n,t in roles(rows,True).items() if t=='H'}
    assert len(expected)==2 and expected==planned['标签'],'H文件身份或范围改变'
    bounds=lambda data:sorted((r['文件'],r['层级'],r['最小键十六进制'],r['最大键十六进制']) for r in data)
    assert bounds(rows)==bounds(planned['参考SST']),'初始逻辑SST身份不同'
    assert (target/'受控标签.txt').read_text().splitlines()==[n+' H' for n in sorted(expected)]
    log=(target/'source/load.zenfs.log').read_text()
    assert '[ERROR]' not in log and '协同池取得目标：阶段=新文件' in log
    assert 'Flush来源W标记：' not in (target/'source/load.log').read_text(),'装载期错误启用W来源'
    proof=dict(通过=True,标签=expected,标签数=2,标签范围='业务Flush',混合Zone=[],
        边界='装载只提供H，没有准备期W；混放必须在业务真实Flush后另行验证，不能在此冒称已形成')
    (target/'真实SST布局核验.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print('仅H初始布局身份通过，业务Flush混放待实际验证',flush=True)


if __name__=='__main__':validate_source(Path(sys.argv[1]))
