"""预注册三场景的独立Python请求校验器；不读取维护结果调整输入。"""
import hashlib
import struct

SCENARIOS=('repeat','spread','switch')
READS=786432
WRITES=1474560
REQUESTS=READS+WRITES


def slot(scenario,window):
    if scenario not in SCENARIOS or not 0<=window<12:
        raise ValueError('场景或窗口越界')
    if scenario=='repeat':return 0
    if scenario=='spread':return window
    return 0 if window<6 else window-5


def planned(scenario,windows=12):
    if windows!=12:raise ValueError('只接受预注册12窗口')
    for window in range(windows):
        base=slot(scenario,window)*65536+4096
        for part in range(2):
            reads=0
            for n in range(61440):
                yield 1,base+n
                while reads<(n+1)*32768//61440:
                    yield 0,(part*32768+reads)%4096
                    reads+=1


def identity(scenario):
    counts=[0,0];h=hashlib.sha256()
    for write,key in planned(scenario):
        counts[write]+=1
        h.update(struct.pack('<BQ',write,key))
    assert counts==[READS,WRITES]
    return dict(场景=scenario,读=counts[0],写=counts[1],请求=REQUESTS,
                操作Key流SHA256=h.hexdigest(),摘要编码='逐项little-endian <BQ：操作位1字节、Key8字节',
                写范围槽=[slot(scenario,w) for w in range(12)])
