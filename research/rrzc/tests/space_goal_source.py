"""业务、空间债务和内容校验分阶段；不改变既有业务客户端或选区源码。"""
from calibration_source import once


def wrapper(source):
    text=once(source,'run_bench() {',
        'experiment_program="$target/db_bench"\nexperiment_args=("${common[@]}")\nrun_bench() {')
    text=once(text,'"$target/db_bench" "${common[@]}" "$@"',
        '"$experiment_program" "${experiment_args[@]}" "$@"')
    anchor='seal_log run;snapshot business\nrequest_trace=""\nhotspot=0;phase verify'
    replacement='''seal_log run;snapshot business
# 业务窗口已封存。收尾两组都用原Greedy，单独计量补足空间的实际成本。
phase goal
saved_greedy_execute=$cal_greedy_execute;saved_gc_gate=$gc_gate
request_trace="";cal_greedy_execute=1;gc_gate="$result/空间终点GC"
experiment_program="$target/space_goal";experiment_args=()
run_bench "$result/goal.zenfs.log" 1073741824 > "$result/goal.log" 2>&1
snapshot goal
experiment_program="$target/db_bench";experiment_args=("${common[@]}")
gc_gate=$saved_gc_gate;cal_greedy_execute=$saved_greedy_execute
hotspot=0;phase verify'''
    return once(text,anchor,replacement)


def smoke(source):
    """直接使用已捕获输入后的当前模拟盘做挂载小测，不额外回放或扩大辅助盘分组。"""
    prefix=source.split('hotspot=1;snapshot before',1)
    if len(prefix)!=2:raise ValueError('空间工具小测缺少唯一业务起点锚点')
    head=once(prefix[0],
        'python3 "$target/logical_input_guest.py" replay "$target" "$result" >> "$result/load.log" 2>&1\n','')
    return head+'''hotspot=0;request_trace="";cal_greedy_execute=1
gc_gate="$result/空间终点GC";experiment_program="$target/space_goal";experiment_args=()
phase goal
run_bench "$result/goal.zenfs.log" 1073741824 > "$result/goal.log" 2>&1
snapshot goal
printf 'done\\n' > "$result/phase.txt"
'''
