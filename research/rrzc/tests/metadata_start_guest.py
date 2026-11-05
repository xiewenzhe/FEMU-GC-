"""仅用真实普通读取补齐已验收元数据起点，不降低或直接写入设备RC。"""
import json
import os


def align(root,query,read_page,stats):
    target=root.parent/'元数据起点目标.json'
    new_reference=root.parent/'启用本轮元数据参考'
    if new_reference.is_file() and not target.exists():
        first_file=root.parent/'元数据参考首臂.txt'
        first=first_file.read_text().strip() if first_file.exists() else 'g1'
        assert first in ('g1','p1') and root.name==first,'只允许预注册首臂建立本轮参考'
        actual=[r for z in range(3) for r in query(z)]
        # 只记录第一轮真实计数；不改设备、不继承旧Open/Active配置的RC目标。
        with target.open('x') as stream:json.dump(actual,stream,ensure_ascii=False,indent=2)
        (root/'元数据参考建立.json').write_text(json.dumps(dict(来源='本轮'+first+'真实逐块查询',
            元数据读取次数=0,元数据起点=actual),ensure_ascii=False,indent=2)+'\n')
    if not target.is_file():return
    expected=json.loads(target.read_text())
    before=[r for z in range(3) for r in query(z)]
    identity=('Zone','块','Channel','LUN','Plane','物理块')
    assert [[r[k] for k in identity] for r in before]==[[r[k] for k in identity] for r in expected], '元数据物理身份不符'
    changed=[(i,a,b) for i,(a,b) in enumerate(zip(before,expected)) if a['RC']!=b['RC']]
    (root/'元数据参考比较.json').write_text(json.dumps(dict(读取前=before,目标=expected,
        差异=[dict(索引=i,实际=a,参考=b) for i,a,b in changed]),ensure_ascii=False,indent=2)+'\n')
    # 仅覆盖本次已采证的首块相差一次；其他形态停止，不能盲扫或降低目标。
    assert len(changed)<=1 and all(a['块']==0 and b['RC']==a['RC']+1 and b['RC']<1024
        for _,a,b in changed),'元数据起点出现未覆盖差异，停止而非改写计数'
    device_before=stats();current=before
    fd=os.open('/dev/nvme0n1',os.O_RDONLY)
    try:
        for index,row,want in changed:
            read_page(fd,row['Zone']*32768)
            current=[r for z in range(3) for r in query(z)]
            assert all(r['RC']==(before[i]['RC']+1 if i==index else before[i]['RC'])
                for i,r in enumerate(current)),'普通读未准确增加指定元数据块'
        assert [r['RC'] for r in current]==[r['RC'] for r in expected], '元数据起点仍不一致'
    finally:os.close(fd)
    after=stats()
    assert after['实际NAND读取']-device_before['实际NAND读取']==len(changed)
    assert after['阈值触发事件']==device_before['阈值触发事件'],'准备读取意外触发RR'
    (root/'元数据起点补齐.json').write_text(json.dumps(dict(通过=True,真实普通读取次数=len(changed),
        元数据读取前=before,元数据读取后=current,目标=expected,设备前=device_before,设备后=after,
        边界='仅补齐已采证的元数据首块差一次；之后仍自然等待31秒，完整逐块检查不放宽'),ensure_ascii=False,indent=2)+'\n')
