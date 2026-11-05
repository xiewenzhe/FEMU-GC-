#!/usr/bin/env python3
"""只读取得FEMU严格RR累积账目；不发数据读取或改变设备状态。"""
import ctypes
import fcntl
import json
import os
import struct

class Command(ctypes.Structure):
    _fields_=[('opcode',ctypes.c_uint8),('flags',ctypes.c_uint8),('rsvd1',ctypes.c_uint16),
        ('nsid',ctypes.c_uint32),('cdw2',ctypes.c_uint32),('cdw3',ctypes.c_uint32),
        ('metadata',ctypes.c_uint64),('addr',ctypes.c_uint64),('metadata_len',ctypes.c_uint32),
        ('data_len',ctypes.c_uint32),('cdw10',ctypes.c_uint32),('cdw11',ctypes.c_uint32),
        ('cdw12',ctypes.c_uint32),('cdw13',ctypes.c_uint32),('cdw14',ctypes.c_uint32),
        ('cdw15',ctypes.c_uint32),('timeout_ms',ctypes.c_uint32),('result',ctypes.c_uint32)]

def read_stats():
    assert ctypes.sizeof(Command)==72
    data=ctypes.create_string_buffer(72);cmd=Command()
    cmd.opcode=2;cmd.nsid=1;cmd.addr=ctypes.addressof(data);cmd.data_len=72;cmd.cdw10=(17<<16)|0xc3
    fd=os.open('/dev/nvme0n1',os.O_RDONLY)
    try:
        raw=bytearray(bytes(cmd));status=fcntl.ioctl(fd,0xc0484e41,raw,True)
        assert status==0,'严格RR日志读取失败：'+str(status)
    finally:os.close(fd)
    names=('版本','启用','实际NAND读取','计入RC读取','自身刷新豁免读取','其他块搬迁计数读取',
        '阈值触发事件','被阻止读取','历史最高RC')
    out=dict(zip(names,struct.unpack('<9Q',data.raw)))
    assert out['版本']==out['启用']==1 and out['实际NAND读取']==out['计入RC读取']+out['自身刷新豁免读取']
    return out

if __name__=='__main__':print(json.dumps(read_stats(),ensure_ascii=False,indent=2))
