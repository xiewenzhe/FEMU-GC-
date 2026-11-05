"""固定完整机制试验；共同中性装载，普通Greedy与在线协同模型直接比较。"""
from calibration_source import once

TOTAL=1048576
READS=786432
WRITES=1474560
REQUESTS=READS+WRITES


def common(text):
    # 完整业务的原生诊断日志超过旧256MiB上限；保留总目录保护线。
    text=once(text,'(256<<20,256<<20)','(512<<20,512<<20)')
    text=once(text,'--num=917504','--num='+str(TOTAL))
    return once(text,'ZENFS_RRZC_POOL_K=1',
        'ZENFS_RRZC_STREAM_CLASSIFY=1 ZENFS_RRZC_POOL_K="$pool_k"')


def source(text):
    text=common(text)
    text=once(text,'run_bench() {','pool_k=0\nrun_bench() {')
    text=once(text,'python3 "$target/pool_flush_guest.py" "$target"',
              'python3 "$target/integrated_pool_guest.py" "$target"')
    return once(text,'--writes=1048576','--writes='+str(TOTAL))


def business(text):
    text=common(text)
    text=once(text,'flush_w=0\nrun_bench() {',
        'pool_k=0\nif [[ $mode == blocks ]]; then pool_k=1; fi\nflush_w=0\nrun_bench() {')
    text=once(text,'--reads=393216 --writes=737280',f'--reads={READS} --writes={WRITES}')
    return once(text,'--reads=917504 --writes=0',f'--reads={TOTAL} --writes=0')
