"""新消融预先固定的真实采样核验；不套用旧实验的事后批数例外。"""
from analyze_stream import rows
from analyze_v1_shadow_stream import require, SECOND

RULE = "真实时序完整批次-v1"


def check_frames(frames, seconds, final_ns, device_gaps):
    require(seconds == 900 and final_ns >= seconds*SECOND, "仅本轮固定900秒观察")
    require(frames and [r[0] for r in frames] == list(range(len(frames))), "观察批次不连续")
    require(all(0 <= b <= e <= final_ns for _, b, e in frames), "查询超出业务范围或时间倒退")
    require(all(a[2] <= b[1] and a[1] < b[1] for a, b in zip(frames, frames[1:])), "查询重叠或开始不递增")
    require(device_gaps and min(device_gaps) > 0, "设备快照未递增或没有连续观测")
    start_gap = frames[0][1]
    end_gap = max(0, seconds*SECOND-frames[-1][2])
    host_gap = max((b[1]-a[1] for a, b in zip(frames, frames[1:])), default=0)
    device_gap = max(device_gaps)
    coverage = max(start_gap, end_gap, host_gap, device_gap) <= 2*SECOND
    return dict(采样规则=RULE,采样协议核验通过=True,逐块轨迹覆盖达标=coverage,
        实际批次=len(frames),理想批次=seconds*2,恰好理想批次=len(frames)==seconds*2,
        首查询开始秒=start_gap/SECOND,末查询结束秒=frames[-1][2]/SECOND,
        末端未覆盖秒=end_gap/SECOND,最大查询开始间隔秒=host_gap/SECOND,
        最大设备快照间隔秒=device_gap/SECOND,
        查询平均微秒=sum(e-b for _, b, e in frames)/len(frames)/1000,
        查询最大微秒=max(e-b for _, b, e in frames)/1000,
        超过目标周期查询次数=sum(e-b > SECOND//2 for _, b, e in frames),
        最终状态时点秒=final_ns/SECOND,补造或插值批次数=0,
        说明="开跑前固定：500ms是目标节拍，不以恰好1800批放行；如覆盖有超过2秒空档，不判定预测命中／误报，业务维护账目独立核验。")


def audit_sampling(run, samples, durations):
    records = rows(run/"recent.csv")
    grouped = {}; frames = []
    keys = ("查询开始纳秒", "查询结束纳秒", "设备会话高位", "设备会话低位", "RR阈值")
    for raw in records:
        batch = int(raw["批次"])
        metadata = tuple(int(raw[k]) for k in keys)
        if batch not in grouped:
            require(batch == len(grouped), "CSV批号跳跃或倒退")
            grouped[batch] = (metadata, set())
            frames.append((batch, *metadata[:2]))
        previous, zones = grouped[batch]
        require(batch == len(grouped)-1 and metadata == previous, "同批元数据不同或批次交错")
        zone = int(raw["区索引"])
        require(zone not in zones and metadata[-1] == 4096, "同批区重复或阈值改变")
        zones.add(zone)
    require(len(grouped) == len(durations) and all(z == set(range(32)) for _, z in grouped.values()),
            "不是完整32区或与逐块解析不符")
    require(len({m[2:4] for m, _ in grouped.values()}) == 1, "采样跨设备会话")
    gaps = [b["time"]-a["time"] for records in samples.values() for a, b in zip(records, records[1:])]
    return check_frames(frames, 900, int(rows(run/"states.csv")[-1]["经过纳秒"]), gaps)
