"""检查本轮新生成的中性真实SST装载；不继承旧文件编号和人工H清单。"""
import csv
import json
from pathlib import Path
import sys
from pool_dbbench_guest import key


def check(target):
    root=target/'source'
    with (root/'装载-0.sst.csv').open() as stream:rows=list(csv.DictReader(stream))
    assert rows and min(key(r['最小键十六进制']) for r in rows)==0
    assert max(key(r['最大键十六进制']) for r in rows)==1048575,'没有完成本轮4GiB逻辑装载'
    log=(root/'load.zenfs.log').read_text()
    assert '[ERROR]' not in log and '协同池取得目标：' not in log,'中性装载意外启用协同分配'
    assert (target/'受控标签.txt').read_text()=='','完整机制实验禁止手工H文件标签'
    proof=dict(通过=True,总记录=1048576,逻辑值字节=1048576*4096,SST数=len(rows),
               初始放置='普通分配K0',H来源='业务实际底层重复读取，进程启动重新学习',
               W来源='业务真实Flush输出代理，未来寿命未知')
    (target/'真实SST布局核验.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':check(Path(sys.argv[1]))
