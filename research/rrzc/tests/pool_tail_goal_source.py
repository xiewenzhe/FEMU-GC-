"""仅插入业务后的共同空间结算，原业务程序和业务环境不改。"""
from calibration_source import once

GOAL=1530117632


def wrapper(text):
    text=once(text,'run_bench() {','experiment_program="$target/db_bench"\nexperiment_args=("${common[@]}")\nrun_bench() {')
    text=once(text,'"$target/db_bench" "${common[@]}" "$@"',
        '"$experiment_program" "${experiment_args[@]}" "$@"')
    return once(text,'seal_log run;snapshot business\nrequest_trace=""\nflush_w=0;hotspot=0;phase verify',
        '''seal_log run;snapshot business
# 业务窗口已封存；原Greedy执行不变，收尾不发前台读写，检查门另起序号。
phase goal
request_trace="";flush_w=0;hotspot=0
gc_gate="$result/空间结算GC"
experiment_program="$target/pool_tail_goal";experiment_args=()
run_bench "$result/goal.zenfs.log" 1530117632 > "$result/goal.log" 2>&1
snapshot goal
experiment_program="$target/db_bench";experiment_args=("${common[@]}")
gc_gate="$result/受控GC"
flush_w=0;hotspot=0;phase verify''')


def smoke(text):
    head=text.split('hotspot=1;snapshot before',1)[0]
    head=once(head,'arm_tag=${RRZC_ARM_TAG:-$mode}','arm_tag=goal-smoke')
    head=once(head,'[[ $arm_tag =~ ^(p1|p2|m1|m2)$ ]]','[[ $arm_tag == goal-smoke ]]')
    head=once(head,'python3 "$target/logical_input_guest.py" replay "$target" "$result" >> "$result/load.log" 2>&1',
        '''# 仅本轮全新隔离模拟盘；完整业务随后独立Reset重放，未保存NAND快照。
[[ $(cat /sys/class/nvme/nvme0/model) == *FEMU* ]]
mkdir "$result/aux"
/home/femu/rocksdb/plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path="$result/aux" > "$result/mkfs.log" 2>&1''')
    return head+'''flush_w=0;hotspot=0;request_trace="";cal_greedy_execute=1
gc_gate="$result/空间结算GC";experiment_program="$target/pool_tail_goal";experiment_args=()
phase goal
run_bench "$result/goal.zenfs.log" 1530117632 > "$result/goal.log" 2>&1
snapshot goal
! grep -Fq '[ERROR]' "$result/goal.zenfs.log"
printf 'done\\n' > "$result/phase.txt"
'''
