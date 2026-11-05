"""只重编交错客户端并链接冻结采样对象，保持选择器与原库不变。"""
import json
from pathlib import Path
import subprocess
import struct
import sys
from build_all_blocks_guest import ROOT, digest
from build_pool_dbbench_guest import build as build_client
from sst_interleaved_source import patched


def build(target):
    path = target / '校准-db_bench_tool.cc'; original = path.read_text()
    integrated=(target/'启用在线协同机制构建').is_file()
    if integrated:
        from calibration_source import once
        header=target/'rrzc_sst_lifetime.h'
        text=once(header.read_text(),'Plan{false,917504,6,65536,4096,2048,8192,persistent_h}',
                  'Plan{false,1048576,12,65536,4096,2048,8192,persistent_h}')
        header.write_text(once(text,'return window*width+hot+index;',
                              'return (window%6)*width+hot+index;'))
    (target / '既有-db_bench_tool.cc').write_text(original); path.write_text(patched(original))
    assert not (target / '同步重编清单.json').exists() and not (target / '复用真实DB程序.json').exists()
    plan = json.loads((target / '冻结对象清单.json').read_text())
    build_client(target)
    assert digest(target / 'fs_colocation.o') == plan['对象SHA256']
    for mode in ('旧分阶段', '持续交错'):
        command = ['env', 'RRZC_SST_LIFETIME_SMOKE=1', 'RRZC_SST_LIFETIME_PERSISTENT_H=1',
            'RRZC_SST_LIFETIME_INTERLEAVED=' + str(int(mode == '持续交错')),
            'RRZC_CAL_TLS_SEED=1', 'RRZC_CAL_SINGLE_BACKGROUND=1',
            'RRZC_CAL_REQUEST_TRACE=' + str(target / (mode + '小测请求.bin')), str(target / 'db_bench'),
            '--db=' + str(target / (mode + '小测库')), '--benchmarks=fillseq,rrzcsstlifetime,readseq,verifychecksum',
            '--num=1024', '--value_size=4096', '--compression_type=none', '--disable_wal=1',
            '--cache_size=0', '--row_cache_size=0', '--threads=1', '--seed=20261023', '--reads=1024', '--writes=480']
        logfile = target / (mode + '普通目录小测.log')
        with logfile.open('w') as log:
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        assert 'SST生命周期完成：读=256，写=480，检查点=10' in logfile.read_text()
    # 与宿主独立生成器逐条核对实际小测记录。
    actual = (target / '持续交错小测请求.bin').read_bytes()
    assert actual[:8] == b'RRZCREQ1' and (len(actual)-8) % 56 == 0
    rows = list(struct.iter_unpack('<7Q', actual[8:])); expected = []
    for part in range(2):
        reads = 0
        for n in range(240):
            expected.append((1, 16+n))
            due = (n+1)*128//240
            while reads < due:
                expected.append((0, (part*128+reads) % 16)); reads += 1
    assert len(rows) == len(expected) == 736
    counts = [0, 0]; last = 0
    for seq, (row, wanted) in enumerate(zip(rows, expected)):
        role, key = wanted
        assert row[:4] == (seq, role, counts[role], key) and last <= row[4] <= row[5] <= row[6]
        counts[role] += 1; last = row[6]
    identity = dict(通过=True, 普通目录小测通过=True, 旧分阶段小测通过=True, 交错小测逐条请求通过=True,
        原客户端源码SHA256=digest(target / '既有-db_bench_tool.cc'), 新客户端源码SHA256=digest(path),
        计划头SHA256=digest(target / 'rrzc_sst_lifetime.h'), 程序SHA256=digest(target / 'db_bench'),
        冻结ZenFS对象SHA256=digest(target / 'fs_colocation.o'), ZenFS未重编=True,
        边界='请求交错改变，须重新验收Greedy；选择器、原GC/RR、采样与空间交接对象冻结')
    (target / '生命周期构建核验.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    build(Path(sys.argv[1]))
