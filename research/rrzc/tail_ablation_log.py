"""显式核验尾部消融接线；原成本重算和源／目标安全复核不能被旁路。"""
from analyze_v1_shadow_stream import fields, require


class TailAblationLog:
    def __init__(self, mode, strategy):
        require(mode in (None, "keep", "bypass"), "未知尾部消融模式")
        require(mode is None or strategy == "guarded", "消融必须使用真实执行入口")
        self.mode = mode
        self.started = False
        self.pending = None
        self.count = 0

    def observe(self, line):
        if "尾部消融：" in line:
            require(self.mode is not None and not self.started, "未请求消融或消融启动重复")
            expected = ("尾部消融：已启用，护栏启用=" + str(int(self.mode == "keep")) +
                        "，仅修改尾部准入，短期建议预算和稳定性复核不变")
            require(line.endswith(expected), "消融启动模式不符或入口拒绝启用")
            self.started = True
        if "尾部消融核验：" in line:
            require(self.mode is not None and self.started and self.pending is None,
                    "消融逐轮记录未启用或重复")
            self.pending = fields(line, "尾部消融核验：")

    def resolve(self, rid, would, original):
        if self.mode is None:
            return original
        require(self.started and self.pending is not None, "消融轮次缺少启动或逐轮记录")
        effective = dict(original)
        if self.mode == "bypass" and would:
            effective.update(分类="消融旁路", 保留原建议=True,
                             原尾部核验=original)
        expected = {"轮次": str(rid), "护栏启用": str(int(self.mode == "keep")),
                    "原会改选": str(int(would)),
                    "原尾部允许": str(int(original["保留原建议"])),
                    "原尾部分类": original["分类"],
                    "生效尾部允许": str(int(effective["保留原建议"])),
                    "生效尾部分类": effective["分类"]}
        require(self.pending == expected, "消融原判断或生效判断与独立重算不符")
        self.pending = None
        self.count += 1
        return effective

    def finish(self, rounds):
        require(self.pending is None, "消融存在未闭合记录")
        require(self.mode is None or self.started and self.count == rounds,
                "消融启动或逐轮覆盖不完整")
