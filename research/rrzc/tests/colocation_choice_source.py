"""四组合独立对象复用原评分；只解除其与expanded入口的历史绑定，不扩大候选。"""
from calibration_source import once
from matched_observation_source import direct as common
from unit_cost_source import direct as unit


def frontend(source):
    text=once(source,'#include "fs_zenfs.h"',
        '#include "fs_zenfs.h"\n#include "rrzc_unit_cost_selector.h"')
    # 原时序入口只供expanded实验使用；本对象显式要求original，两臂完全相同。
    text=once(text,'!direct_enabled || !candidate_pool.expanded)',
        '!direct_enabled || candidate_pool.expanded)')
    text=once(text,'只接受直接选区及expanded候选下的显式1',
        '四组合独立对象只接受直接选区及original候选下的显式1')
    return text


def selection(source):
    return unit(common(source))
