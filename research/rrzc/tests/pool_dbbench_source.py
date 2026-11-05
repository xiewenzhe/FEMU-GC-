"""只为独立实验生成包装器；原定制请求、缓存和维护规则保留。"""
from calibration_source import once


def common(text):
    text=once(text,'ZENFS_RRZC_CANDIDATE_POOL=expanded','ZENFS_RRZC_CANDIDATE_POOL=original')
    text=once(text,'ZENFS_MAINT_DIAG=1',
        'ZENFS_RRZC_MAINTENANCE_COVER=1 ZENFS_RRZC_UNIT_COST=1 '
        'ZENFS_RRZC_POOL_K=1 ZENFS_RRZC_POOL_TAGS="$target/受控标签.txt" ZENFS_MAINT_DIAG=1')
    # 冻结ZenFS对象没有逐请求设备摘要接线，不能输出空摘要冒充已观测设备命令。
    text=text.replace('RRZC_CAL_IO_TRACE="${request_trace:+$result/请求设备摘要.bin}" ','')
    return text


def source(text):
    text=common(text)
    begin=text.index('enabled=0;greedy=1;planning=()')
    end=text.index('common=(',begin)
    text=text[:begin]+'''cal_greedy_execute=1
enabled=1;greedy=0
planning=(ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0)
'''+text[end:]
    text=once(text,'ZENFS_MAINT_DIAG=1',
        'RRZC_CAL_COMMON_OBSERVATION=1 RRZC_CAL_GREEDY_EXECUTE=1 ZENFS_MAINT_DIAG=1')
    return once(text,'python3 "$target/logical_input_guest.py" capture',
        'python3 "$target/pool_dbbench_guest.py" "$target"\n'
        'python3 "$target/logical_input_guest.py" capture')


def business(text):
    text=common(text)
    text=once(text,'[[ $arm_tag =~ ^[a-z0-9-]+$ ]]','[[ $arm_tag =~ ^(p1|p2|m1|m2)$ ]]')
    return once(text,'"$target/g1" matched','"$target/p1" matched')
