"""准备期按已有SST键区间生成显式标签；不是在线分类或寿命预测。"""
import csv
import json
from pathlib import Path
import re
import sys


def table(path):
    with path.open() as stream:return list(csv.DictReader(stream))


def key(text):
    value=bytes.fromhex(text);assert len(value)==16 and value[8:]==b'00000000','键编码不符'
    return int.from_bytes(value[:8],'big')


def roles(rows,cohort_zero=False):
    result={}
    for row in rows:
        assert re.fullmatch(r'/[0-9]+\.sst',row['文件'])
        low,high=key(row['最小键十六进制']),key(row['最大键十六进制']);assert low<=high<917504
        overlaps=lambda a,b:low<b and high>=a
        # 预先声明的前六个轮转阶段。H可能同时含写热点，标签不是互斥Key分类。
        bases=range(0,65536 if cohort_zero else 393216,65536)
        hot=any(overlaps(base,base+4096) for base in bases)
        writes=any(overlaps(base+4096,base+65536) for base in bases)
        if hot or writes:result['/rrzc-dbbench'+row['文件']]='H' if hot else 'W'
    assert {'H','W'}<=set(result.values()),'准备期缺少两类真实SST'
    return result


def validate_source(target):
    source=target/'source';rows=table(source/'装载-0.sst.csv')
    planned=json.loads((target/'受控标签来源.json').read_text())
    scope=planned.get('标签范围','六窗');assert scope in ('六窗','窗口0'),'未知受控标签范围'
    actual=roles(rows,scope=='窗口0')
    assert actual==planned['标签'],'本轮装载SST编号/键区间与标签参考不同，停止而非重贴有利标签'
    bounds=lambda data:sorted((r['文件'],r['层级'],r['最小键十六进制'],r['最大键十六进制']) for r in data)
    assert bounds(rows)==bounds(planned['参考SST'])
    kinds={}
    for ext in table(source/'装载-0.extent.csv'):
        tag=actual.get(ext['文件'])
        if tag:kinds.setdefault(int(ext['Zone'])//(128<<20),set()).add(tag)
    mixed=sorted(z for z,tags in kinds.items() if {'H','W'}<=tags)
    assert mixed,'K1装载没有形成不同H/W SST共享Zone，停止收益比较'
    text=(source/'load.zenfs.log').read_text()
    assert '协同池取得目标：阶段=新文件' in text and '[ERROR]' not in text
    assert (target/'受控标签.txt').read_text().splitlines()==[name+' '+tag for name,tag in sorted(actual.items())], '运行标签与核验清单不同'
    proof=dict(通过=True,混合Zone=mixed,标签数=len(actual),标签=actual,标签范围=scope,
        边界='准备期历史SST编号+预注册键区间标签；不保证短寿命，新Compaction输出未分类时回退普通放置')
    (target/'真实SST布局核验.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print('真实SST标签与混合布局核验通过，混合Zone='+str(mixed))


if __name__=='__main__':validate_source(Path(sys.argv[1]))
