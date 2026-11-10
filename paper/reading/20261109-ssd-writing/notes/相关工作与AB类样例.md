# 相关工作与 A/B 类样例

范围和类别核验限制见[阅读索引](../README.md)。以下只记录已读材料支持的观察，不把其他作者的结果当成本项目结果。

## FineRR-ZNS（DAC 2025，A 类样例）

来源：[作者 PDF](https://jiemingyin.github.io/docs/DAC2025.pdf)。定位：§III、图1–2；§IV、图3；§V。

文章内容：动机用 zone 级与更细粒度刷新开销、区内读计数差异说明问题；设计用 remapping、reconstruction 和保留副本串起读写路径；评估分别看迁移、擦除及吞吐，并说明若干理想基线的约束。

写作借鉴：先呈现粗粒度维护为何浪费，再交代机制如何改变路径。理想上界应标清不可直接部署的条件。我们的关注是放置与 ZC/RR 协同；不能说自己已经实现本文的块级重映射，也不能仅靠 RR 下降证明总维护下降。

## ZNS（USENIX ATC 2021，A 类样例）

来源：[会议页](https://www.usenix.org/conference/atc21/presentation/bjorling)、[会议 PDF](https://www.usenix.org/system/files/atc21-bjorling.pdf)。定位：图1；§2.3、§3；§4.2；§5。

文章内容：先解释接口成本，再说明设备与主机的职责、zone 语义和资源权衡，然后展示 RocksDB/ZenFS 路径与评估。比较中交代设备、容量与预留空间等条件，区分设备层和端到端指标。

写作借鉴：背景只保留支撑研究问题的知识；系统图标出决策与维护归属。Open 和 Active 不是同一资源，zone 容量与大小也应区分。论文讨论的设备职责不能直接被改写成“所有 ZNS SSD 都由主机承担全部可靠性维护”。

## Design Tradeoffs for SSD Reliability（FAST 2019，A 类样例）

来源：[会议页](https://www.usenix.org/conference/fast19/presentation/kim-bryan)、[会议 PDF](https://www.usenix.org/system/files/fast19-kim-bryan.pdf)。定位：表1、§3–4、图8、§5、结论及尾注。

文章内容：先刻画 ECC、重读、冗余与 scrubbing 的相互代价，再提出组合管理方案。结果同时报告平均/尾部性能与写放大；oracle scrubbing 明确依赖实践中不可得的信息。也讨论并非所有场景都占优。

写作借鉴：先说明局部优化的反作用，再引出协调决策。当前项目读计数阈值模型的验证，不等于芯片误码率或商业 SSD 可靠性验证。历史观测、预测值与 oracle 信息需要明确区分。

## Same-Zone Compaction（MSST 2024，B 类样例）

来源：[会议 PDF](https://www.msstconference.org/MSST-history/2024/Papers/msst24-9.2.pdf)，网页全文可读，本地下载失败。定位：§III、§V-B、图7、§VII-A、图13–16。

文章内容：把放置与 compaction 选择联系起来，并解释减少 ZC 阻塞也可能增加 merge-sort 代价。评估比较两类机制的四种组合，检查收益来源、阈值和性能副作用。

写作借鉴：消融要隔离机制贡献；同时展示代价，不只展示有利计数。本文的 compaction victim 与我们的 ZC victim 不是同一概念。其寿命表述不能替代本项目的物理擦除或寿命测量。

## 高相关但尚未完成全文阅读的工作

|工作|已确认范围|后续需要核对|
|---|---|---|
|[DZR，JSA 2024](https://doi.org/10.1016/j.sysarc.2024.103159)|出版方预览与[作者仓库](https://github.com/hyu-splab/DZR-rocksdb)可定位工作；尚无完整正文|并行 compaction、zone 竞争与动态分配的完整设计和资源约束；不能把预览当全文|
|[SWEEP，TODAES 2026](https://doi.org/10.1145/3795519)|可见材料明确涉及读热/写热数据共置与 read reclaim；部分正文可读，PDF 被拒绝|准确分类、容量预算、迁移路径、GC/RR 总账与统计窗口；必须补齐正文和图表|
|[ColdMap，ICS 2026](https://doi.org/10.1145/3797905.3807851)|更新：已取得本地14页全文并读完|详见[后续全文精读](../../20261109-zns-deep-reading/notes/ColdMap-全文逻辑.md)，其中保留公式方向和作者代码差异；不再视为缺全文|

特别需要保留的新颖性边界：读热/写热数据共置已有相关工作，因此不能把“首次把两类数据放在一起”预先写成贡献。受限资源下的准入、回退和总维护收益是否构成可支持的区别，仍需全文比较和本项目证据共同决定。这是当前审慎判断，不是已经完成的新颖性证明。
