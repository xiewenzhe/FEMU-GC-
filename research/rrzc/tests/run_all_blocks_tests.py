#!/usr/bin/env python3
"""编译真实设备查询函数和独立模型核；只用内存替身，不访问块设备。"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
def function(source,name):
    start=source.index('static uint16_t '+name+'(')
    end=source.index('\n}',start)+2
    return source[start:end]+'\n'

def main():
    with tempfile.TemporaryDirectory(prefix='rrzc-all-blocks-') as folder:
        build=Path(folder);wire=(ROOT/'hw/femu/nvme.h').read_text()
        (build/'wire.inc').write_text(wire[wire.index('enum ZnsRrRecentFlags '):wire.index('} ZnsRrRecentEntry;')+len('} ZnsRrRecentEntry;')])
        (build/'device.inc').write_text(function((ROOT/'hw/femu/zns/zns.c').read_text(),'zns_rr_blocks_log'))
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
        for compiler,source,extra in (
            ('cc','test_all_blocks_device.c',['-std=gnu11','-Wno-sign-compare','-I'+str(build),'-pthread']),
            ('g++','test_all_blocks.cc',['-std=c++17','-I'+str(ROOT/'research/rrzc/model'),'-pthread']),
            ('g++','test_all_blocks_io.cc',['-std=c++17','-I'+str(ROOT/'research/rrzc/model'),'-pthread'])):
            binary=build/source
            subprocess.run([compiler,'-Wall','-Wextra','-Werror','-fsanitize=address,undefined',*extra,
                str(ROOT/'research/rrzc/tests'/source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True,env=env)

if __name__=='__main__':main()
