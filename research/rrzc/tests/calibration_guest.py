#!/usr/bin/env python3
"""阶段间只读RC查询、起跑资格核验；不足证据时停止，不挑选有利运行。"""
import csv
import ctypes
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import time
from strict_stats_guest import Command
from strict_stats_guest import read_stats


def blocks(only_zone=None):
    rows=[]
    fd=os.open('/dev/nvme0n1',os.O_RDONLY)
    try:
        for z in range(64) if only_zone is None else (only_zone,):
            data=ctypes.create_string_buffer(56+128*88);cmd=Command()
            cmd.opcode=2;cmd.nsid=1;cmd.addr=ctypes.addressof(data);cmd.data_len=len(data)
            numd=len(data)//4-1;cmd.cdw10=((numd&65535)<<16)|0xc4;cmd.cdw11=(z<<16)|(numd>>16)
            assert fcntl.ioctl(fd,0xc0484e41,bytearray(bytes(cmd)),True)==0,'C4查询失败'
            raw=data.raw
            assert struct.unpack_from('<4I',raw)==(1,88,64,128),'C4头不符'
            assert struct.unpack_from('<Q',raw,16)[0]==1024,'RR阈值不符'
            session=struct.unpack_from('<2Q',raw,24)
            assert any(session),'设备会话无效'
            for i in range(128):
                a=struct.unpack_from('<7Q8I',raw,56+i*88)
                assert a[7]==z and a[8]==i and a[9]<8 and a[4]<=1024 and a[-1]==0,'块身份或RC异常'
                rows.append(dict(Zone=z,块=i,设备会话=session,代际=a[1],快照=a[2],代际起点=a[3],RC=a[4],
                    十秒增量=a[5],三十秒增量=a[6],Channel=a[9],LUN=a[10],Plane=a[11],物理块=a[12],标志=a[13]))
    finally:os.close(fd)
    return rows


def raw_read(fd,page):
    # 标准NVMe普通读，4KiB对齐；不发送写入、Reset或私有迁移命令。
    data=ctypes.create_string_buffer(4096);cmd=Command()
    cmd.opcode=2;cmd.nsid=1;cmd.addr=ctypes.addressof(data);cmd.data_len=4096
    lba=page*8;cmd.cdw10=lba&0xffffffff;cmd.cdw11=lba>>32;cmd.cdw12=7;cmd.timeout_ms=5000
    assert fcntl.ioctl(fd,0xc0484e43,bytearray(bytes(cmd)),True)==0,'准备普通读失败'


def warmup(root):
    if ((root.parent/'元数据起点目标.json').is_file() or
            (root.parent/'启用本轮元数据参考').is_file()):
        from metadata_start_guest import align
        align(root,blocks,raw_read,read_stats)
    target=32
    extents=table(root/'装载-0.extent.csv')
    zones=sorted({int(r['Zone'])//(128<<20) for r in extents if r['文件'].endswith('.sst')})
    assert zones and all(3<=z<64 for z in zones),'准备区不属于本次SST数据区'
    before=read_stats();proof=[];z=None;first=[];current=[];mapped={}
    fd=os.open('/dev/nvme0n1',os.O_RDONLY)
    try:
        for z in zones:
            mapped={};first=blocks(z);current=first
            assert max(r['RC'] for r in first)<target,'起始风险超过准备目标，拒绝减少或伪造计数'
            mapped={};reads=0
            # 只扫描实际SST覆盖范围的头部，允许未映射缓存页不增加NAND计数。
            end=max(int(r['起点'])+int(r['字节'])-z*(128<<20) for r in extents
                    if int(r['Zone'])==z*(128<<20) and r['文件'].endswith('.sst'))
            for p in range(min(2048,(end+4095)//4096)):
                raw_read(fd,z*32768+p);reads+=1;after=blocks(z)
                assert all(a['代际']==b['代际'] and a['设备会话']==b['设备会话'] for a,b in zip(current,after)), '准备期间发生Reset或设备会话改变'
                changed=[i for i,(a,b) in enumerate(zip(current,after)) if a['RC']!=b['RC']]
                assert len(changed)<=1 and all(after[i]['RC']==current[i]['RC']+1 for i in changed),'普通单页读没有对应单块增长'
                if changed:mapped.setdefault(changed[0],z*32768+p)
                current=after
                if len(mapped)==128:break
            assert all(r['RC']==0 or i in mapped for i,r in enumerate(current)),'存在未找到真实读取页的非零RC块'
            for i,page in sorted(mapped.items()):
                missing=target-current[i]['RC'];assert missing>=0,'准备读取超过目标'
                for _ in range(missing):raw_read(fd,page);reads+=1
                after=blocks(z)
                assert after[i]['RC']==target and all(a['RC']==b['RC'] for j,(a,b) in enumerate(zip(current,after)) if j!=i), '准备读取未准确增加同一个物理块'
                current=after
            assert all(r['RC']==(target if i in mapped else 0) for i,r in enumerate(current)), '准备区逐块计数不闭合'
            proof.append(dict(Zone=z,可读物理块=len(mapped),块到实测逻辑页=mapped,起始读数和=sum(r['RC'] for r in first),
                结束读数和=sum(r['RC'] for r in current),普通读请求数=reads,真实NAND读增量=sum(r['RC'] for r in current)-sum(r['RC'] for r in first)))
            print('真实准备读取：Zone='+str(z)+'，可读块='+str(len(mapped))+'，目标RC=32',flush=True)
    except BaseException as error:
        (root/'准备失败证据.json').write_text(json.dumps(dict(错误=repr(error),已完成区=proof,
            当前Zone=z,本区起始=first,最近帧=current,已识别逻辑页=mapped),ensure_ascii=False,indent=2)+'\n')
        raise
    finally:os.close(fd)
    after=read_stats()
    assert after['阈值触发事件']==before['阈值触发事件'] and after['自身刷新豁免读取']==before['自身刷新豁免读取'],'准备过程意外触发维护'
    assert after['计入RC读取']-before['计入RC读取']==sum(r['真实NAND读增量'] for r in proof),'设备账目与准备增量不一致'
    (root/'真实准备读取.json').write_text(json.dumps(dict(目标RC=target,逐区=proof,设备前=before,设备后=after,
        限制='仅真实普通读增加计数；未编程块保留0；不修改设备RC或触发维护'),ensure_ascii=False,indent=2)+'\n')
    # 等真实近期窗口自然衰减，避免把准备读速率当作业务风险。
    print('准备读取已结束，等待31秒清除近期窗口贡献',flush=True)
    time.sleep(31)


def table(path):
    with path.open() as f:return list(csv.DictReader(f))


def state(root):
    sst=table(root/'装载-0.sst.csv')
    # 不以随机会话及元数据时间戳要求SST字节完全一致；检查键分界和数据页映射。
    ranges=sorted((r['文件'],int(r['层级']),r['最小键十六进制'],r['最大键十六进制']) for r in sst)
    extents=sorted((r['文件'],int(r['Zone']),int(r['起点']),(int(r['字节'])+4095)//4096)
                   for r in table(root/'装载-0.extent.csv'))
    rc=json.loads((root/'blocks.before.json').read_text())
    assert len(rc)==64*128 and len({(r['Zone'],r['块']) for r in rc})==64*128,'RC覆盖不完整'
    # 会话/Reset代际真实保存；不同轮不能要求代际数值相同，不能跨代际拼接读取增量。
    counts=[(r['Zone'],r['块'],r['Channel'],r['LUN'],r['Plane'],r['物理块'],r['RC']) for r in rc if r['Zone']>=3]
    space=json.loads((root/'space.before.json').read_text())
    zones=[(r['编号'],r['WP'],r['剩余']) for r in space['Zone'] if r['编号']>=3]
    return dict(键范围=ranges,数据页映射=extents,数据区RC=counts,数据区空间=zones)


def gate(root,reference,rc_policy='exact'):
    assert rc_policy in ('exact','low','matched'),'未知RC准入模式'
    current=state(root)
    # 性能重复可显式选择阈值1%以内的低RC起点；不改设备计数，严格相同模式仍为默认。
    rc_limit=1024//100
    current_max=max(r[-1] for r in current['数据区RC'])
    if rc_policy=='low':
        assert current_max<=rc_limit,'起始RC超过阈值1%，拒绝进入低风险重复'
    if rc_policy=='matched':
        proof=json.loads((root/'真实准备读取.json').read_text())
        assert proof['目标RC']==32,'真实准备读取证据缺失或目标不符'
        sst_zones={row[1]//(128<<20) for row in current['数据页映射'] if row[0].endswith('.sst')}
        raw=json.loads((root/'blocks.before.json').read_text())
        assert all(r['RC'] in (0,32) and r['十秒增量']==r['三十秒增量']==0 for r in raw if r['Zone'] in sst_zones), 'SST准备读数或近期窗口尚未对齐'
        assert all(r['RC']<=rc_limit for r in raw if r['Zone']>=3 and r['Zone'] not in sst_zones),'非SST区存在高风险起点'
        assert set(r['Zone'] for r in proof['逐区'])==sst_zones,'准备读取未覆盖全部SST区'
        for item in proof['逐区']:
            assert sum(r['RC']==32 for r in raw if r['Zone']==item['Zone'])==item['可读物理块'],'准备映射与真实计数不一致'
        current['数据区RC']=[r for r in current['数据区RC'] if r[0] in sst_zones]
        current['完整可观测起点']=observable_start(root)
    if root==reference:
        return dict(通过=True,角色='首轮参考',检查项=list(current),RC模式=rc_policy,当前最高RC=current_max,
                    RC上限=rc_limit if rc_policy=='low' else None,
                    限制='阶段间顺序查询，非跨层原子快照；参考轮本身不证明重复性')
    previous=state(reference)
    if rc_policy=='matched':
        previous['数据区RC']=[r for r in previous['数据区RC'] if r[0] in sst_zones]
        previous['完整可观测起点']=observable_start(reference)
    if (root/'逻辑输入回放.json').exists():
        replay=json.loads((root/'逻辑输入回放.json').read_text())
        old=json.loads((reference/'逻辑输入回放.json').read_text())
        assert replay==old,'固定逻辑输入或串行命令序列不一致'
        def samples(path):
            return [(r['Zone'],r['块到实测逻辑页']) for r in json.loads((path/'真实准备读取.json').read_text())['逐区']]
        assert samples(root)==samples(reference),'重建后的物理块访问样本不一致，停止业务'
    a,b=current['数据区RC'],previous['数据区RC']
    identity_equal=[r[:-1] for r in a]==[r[:-1] for r in b]
    maximum_difference=max(abs(x[-1]-y[-1]) for x,y in zip(a,b))
    rc_equal=a==b
    different=[name for name in current if current[name]!=previous[name]]
    if rc_policy=='low':
        assert identity_equal and max(r[-1] for r in b)<=rc_limit,'物理身份不同或参考RC超过低风险上限'
        different=[name for name in different if name!='数据区RC']
    result=dict(通过=not different,差异项=different,参考目录=str(reference),
        RC模式=rc_policy,RC严格相同=rc_equal,物理块身份相同=identity_equal,
        当前最高RC=current_max,参考最高RC=max(r[-1] for r in b),RC上限=rc_limit if rc_policy=='low' else None,
        RC不同块数=sum(x[-1]!=y[-1] for x,y in zip(a,b)),最大RC差=maximum_difference,
        当前数量={k:len(v) for k,v in current.items()},参考数量={k:len(v) for k,v in previous.items()})
    (root/'起始差异.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    assert not different,'起始状态不一致，停止业务而非混入重复统计：'+','.join(different)
    return result


def observable_start(root):
    """补足matched门控：全部Zone状态/有效量及全部块读状态，仍不称完整内存快照。"""
    zones=json.loads((root/'zones.before.json').read_text())['zone_list']
    raw=json.loads((root/'blocks.before.json').read_text())
    space=json.loads((root/'space.before.json').read_text())['Zone']
    assert len(zones)==64 and len(raw)==8192,'完整起点覆盖不足'
    identities=[(r['Zone'],r['块']) for r in raw]
    assert len(set(identities))==8192,'完整起点块身份重复'
    return dict(
        Zone状态=sorted((r['slba'],r['cap'],r['wp'],r['state'],r['attrs'],r['attrs_info']) for r in zones),
        Zone有效量=sorted((r['编号'],r['有效'],r['容量']) for r in space),
        全部块=sorted((r['Zone'],r['块'],r['Channel'],r['LUN'],r['Plane'],r['物理块'],
            r['RC'],r['十秒增量'],r['三十秒增量'],r['标志']) for r in raw))


if __name__=='__main__':
    if sys.argv[1]=='blocks':out=blocks()
    elif sys.argv[1]=='warmup':warmup(Path(sys.argv[2]));sys.exit(0)
    elif sys.argv[1]=='gate':out=gate(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4] if len(sys.argv)>4 else 'exact')
    else:raise ValueError('未知校准子命令')
    print(json.dumps(out,ensure_ascii=False,indent=2))
