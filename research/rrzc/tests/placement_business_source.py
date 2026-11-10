"""只适配冻结业务入口的放置配置与组成采证；请求、GC/RR和选区环境保持原值。"""
from calibration_source import once


def wrapper(text):
    text=once(text,'( $1 == greedy || $1 == blocks )','( $1 == G0 || $1 == G1 || $1 == G2 )')
    text=once(text,'mode=$1','placement=$1\nmode=greedy')
    text=once(text,'pool_k=0\nif [[ $arm_tag == m1 ]]; then pool_k=1; fi\nflush_w=0\n','')
    old='ZENFS_RRZC_POOL_FLUSH_W="$flush_w" ZENFS_RRZC_STREAM_CLASSIFY=1 ZENFS_RRZC_POOL_K="$pool_k" ZENFS_RRZC_POOL_TAGS="$target/受控标签.txt"'
    text=once(text,old,'ZENFS_RRZC_PLACEMENT_MODE="$placement" ZENFS_RRZC_PLACEMENT_TRACE="${request_trace:+$result/组成.bin}"')
    text=once(text,'flush_w=1;hotspot=1;phase run','hotspot=1;phase run')
    text=once(text,'flush_w=0;hotspot=0;phase verify','hotspot=0;phase verify')
    assert '--reads=786432 --writes=1474560' in text
    assert 'RRZC_CAL_GREEDY_EXECUTE="$cal_greedy_execute"' in text
    return text
