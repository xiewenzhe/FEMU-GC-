"""校验并输出论文平台配置；不启动客机、不格式化设备、不覆盖旧实验。"""
import argparse
import json
from pathlib import Path

DEFAULT = Path(__file__).with_name('configs') / 'finerr-zns.json'


def load(path=DEFAULT):
    profile = json.loads(Path(path).read_text())
    h, n, m = (profile[k] for k in ('硬件', '命名空间', '维护'))
    keys = ('通道数', '每通道Chip数', '每ChipDie数', '每DiePlane数',
            '每PlaneBlock数', '每BlockPage数', '物理页字节')
    if any(type(h[k]) is not int or h[k] <= 0 for k in keys):
        raise ValueError('物理几何必须为正整数')
    blocks = h['通道数'] * h['每通道Chip数'] * h['每ChipDie数'] * h['每DiePlane数']
    zone = blocks * h['每BlockPage数'] * h['物理页字节']
    if (blocks != n['每Zone物理块数'] or zone != n['Zone字节'] or
            n['Zone数量'] != h['每PlaneBlock数'] or
            zone * n['Zone数量'] != n['容量字节']):
        raise ValueError('物理几何、Zone数量或总容量不一致')
    if (h['Cell类型'] != 'QLC' or h['每BlockPage数'] % 4 or
            h['物理页字节'] not in (4096, 16384)):
        raise ValueError('单元类型或物理页不受当前配置支持')
    if (not 0 < m['GC启动百分比'] < 100 or m['RR阈值'] <= 0 or
            not 0 <= m['模型最小回收百分比'] < 100 or
            n['Zone字节'] * m['模型最小回收百分比'] != m['模型最小回收字节'] * 100):
        raise ValueError('维护阈值或容量比例不一致')
    keep = profile['沿用条件']
    active, opened = keep['最大活动Zone数'], keep.get('最大打开Zone数', 0)
    # 兼容冻结的旧配置：未提供Open上限时仍为0，不改变历史实验参数。
    for name, value in (('Active', active), ('Open', opened)):
        if type(value) is not int or value < 0 or value > n['Zone数量']:
            raise ValueError(f'{name}上限必须为不超过Zone数量的非负整数，0表示无限制')
    if active and active <= 2:
        raise ValueError('Active上限必须为ZenFS两个预留名额之外保留普通I/O余量')
    if opened and opened <= 3:
        raise ValueError('Open上限必须覆盖ZenFS两个预留名额及非优先分配保留令牌')
    if active and opened and opened > active:
        raise ValueError('Open上限不能大于Active上限')
    return profile


def settings(profile):
    h, n, m, keep = (profile[k] for k in ('硬件', '命名空间', '维护', '沿用条件'))
    device = dict(devsz_mb=n['容量字节'] // (1 << 20), namespaces=1, femu_mode=3,
        zns_num_ch=h['通道数'], zns_num_lun=h['每通道Chip数'] * h['每ChipDie数'],
        zns_num_plane=h['每DiePlane数'], zns_num_blk=h['每PlaneBlock数'],
        zns_page_size=h['物理页字节'], zns_pgs_per_blk=h['每BlockPage数'],
        zns_flash_type=4, zns_pg_rd_lat=h['读取纳秒'], zns_pg_wr_lat=h['编程纳秒'],
        zns_blk_er_lat=h['擦除纳秒'], zns_rr_threshold=m['RR阈值'],
        zns_rr_history='on' if keep['逐块近期统计'] else 'off',
        zns_max_active=keep['最大活动Zone数'],
        zns_max_open=keep.get('最大打开Zone数', 0))
    return dict(设备参数='femu,' + ','.join(f'{k}={v}' for k, v in device.items()),
        公共ZenFS环境={'ZENFS_RRZC_RECLAIM_BYTES': str(m['模型最小回收字节']),
                      'ZENFS_RRZC_CONTENT_GUARD': '1' if keep['内容隔离'] else '0'},
        设备验收参数={'Zone数': n['Zone数量'], '每Zone块数': n['每Zone物理块数'],
                      'RR阈值': m['RR阈值'], 'Zone字节': n['Zone字节']},
        内存预算说明='单台NAND后端32GiB，加客机4GiB及映射/统计/进程开销，先按约40GiB预算，必须串行。',
        使用边界='参数源与设备功能验收，不等于完整YCSB已接线。旧脚本/分析器中的32Zone、64块、RR4096及192MiB断言不能直接用于新配置。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=DEFAULT)
    parser.add_argument('--device', action='store_true', help='仅打印设备参数，不启动')
    args = parser.parse_args()
    result = settings(load(args.profile))
    print(result['设备参数'] if args.device else json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
