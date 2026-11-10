# Introduction v1：英文稿与证据核对

日期：2026年11月9日。用户明确要求先写Introduction，因此先完成该节工作稿；不把之前建议的章节起草顺序当作限制。正文位置：[main.tex](../overleaf/main.tex)；引用库：[references.bib](../overleaf/references.bib)。

## 这一版写了什么

采用技术问题逐步收窄的引言结构。先交代ZC/RR的数据搬运，再介绍不同决策位置的已有工作，最后把问题落到资源有限时布局的后续演化。正文含7段与3条贡献；确切近似词数见 `Introduction-v1-verification.json`，不把LaTeX命令或引用键当作正文词。

|段落|任务|依据与边界|
|---|---|---|
|P1|说明ZNS、LSM与ZC的联系|ZNS2021原文§1–4；CAZA/OAZA。只描述接口、文件失效与搬运，不复述作者宽泛的设备性能优势|
|P2|引入RR并说明本项目研究范围|FineRR-ZNS正文§III–IV；根目录研究计划及阶段三/四记录。项目的block RC、host zone RR是本平台模型，不推及全部商用设备|
|P3|区分CAZA、OAZA、ColdMap和FineRR的决策位置，引出具体问题|已读本地原文。不声称系统已优于它们，不声称第一次共置；SWEEP/DZR全文未完成，不补其细节|
|P4|解释共置的有利与不利时序，以及迁移重建混合|第五阶段H1/2/4/5/15、H9/H12与普通回退实例。条件式说明机会，不将离线退役量当逐事件反事实节省|
|P5|解释Open/Active预算、必要迁移与标签信息的挑战|研究计划；第四/五阶段目标与资源证据。flush provenance只表示来源，不表示真实短寿命|
|P6|概述已实现的固定预算放置和评估边界|G0/G1/G2独立实现、阶段三设备、阶段四同Greedy消融。没有把W历史候选、动态K或联合选择器写入最终方法|
|P7+贡献|概括已有机制认识、轨迹分析与空间/性能权衡|第四阶段一轮受控消融、第五阶段离线重建。保留较少复制不等于比G1吞吐更高的观察；不填50%为最终泛化收益|

## 可以追溯的本项目依据

- [研究计划](../../ZNS_GC_RR_Research_Plan.docx)：系统范围与主问题；是计划，不是完成证明。
- [阶段三实施与验证](../../research/rrzc/results/placement-ablation-20261105/阶段三/实施与验证.md)：模式、自然RR、内容及重挂载功能范围。
- [阶段四实施与结果分析](../../research/rrzc/results/placement-ablation-20261105/阶段四/实施与结果分析.md)：G0/G1/G2、总量/分项/空间/性能与单轮范围。
- [阶段四业务结果补充核验](../../research/rrzc/results/placement-ablation-20261105/阶段四/业务结果补充核验.json)：复制、映射、请求与数值口径。
- [阶段五机制报告](../../research/rrzc/results/placement-ablation-20261105/阶段五/第五阶段：数据放置收益机制与剩余成本归因报告.md)：后续失效、旧W再混入、普通区陪迁与性能归因限制。
- [论文写作蓝图](JSA-论文写作蓝图.md)及其 `evidence-snapshot.json`：本次沿用的范围；其中状态是当时快照，不据此推断当前其他实验运行情况。

本轮回读上述报告与原文，未重新执行原始归档审计或实验。项目结果均限定在受控工作负载；引言中的贡献3是测量与分析贡献，不是已完成外部全基线性能比较。

## 五条文献与支撑范围

|引用键|主要来源|支撑的具体陈述|
|---|---|---|
|bjorling2021zns|[ZNS本地PDF](../reading/20261109-ssd-writing/sources/atc2021-zns.pdf)，会议正式稿|ZNS接口和RocksDB/ZenFS；不声称主机接管设备所有可靠性职责|
|lee2022caza|[CAZA本地PDF](../../research/rrzc/baselines/materials/caza-hotstorage2022.pdf)，首页与设计|利用compaction关系的放置；2022原始工作，非2024派生实现|
|shen2025oaza|[OAZA期刊版PDF](../../research/rrzc/baselines/materials/oaza-gc.pdf)，首页、§4|层级/OR分配及配套GC；明确是2025期刊扩展版|
|byeon2026coldmap|[ColdMap本地PDF](../../research/rrzc/baselines/materials/ColdMap_Compaction_Aware_Cost_Benefit_Zone_Cleaning_for_ZNS-Based_Key-Value_Stores.pdf)，首页、§4–5|模拟compaction、冷度聚合及清理选区，不引用有歧义的收益数字|
|li2025finerr|[作者PDF本地副本](../reading/20261109-ssd-writing/sources/dac2025-finerr-zns.pdf)，§III–IV；下述机构记录|刷新粒度、remapping与reconstruction；不重复“所有ZNS无RR”的过强表述|

FineRR-ZNS作者顺序与题目以本地作者PDF为准。会议名称、年份和DOI由作者所属机构的[研究成果记录](https://researchportal.murdoch.edu.au/esploro/outputs/conferencePaper/FineRR-ZNS-Enabling-Fine-Granularity-Read-Refreshing-for/991005821549407891)检索返回文本交叉核对，记录为62nd ACM/IEEE DAC2025、DOI `10.1109/DAC63849.2025.11132791`；该网页随后直接打开返回403，未冒称读取了其全文。其他四条的正文与核心元数据来自已有本地正式/作者PDF。

## 用词与主张检查

- hot表示读取相关特征，ColdMap的coldness按其预测失效语义描述；没有将二者混为一轴。
- 条件性机会用if/may；平台行为与已实现机制用现在时。结果受控范围紧邻show。
- 不写first、optimal、guarantee、significantly outperforms或提高真实NAND可靠性。
- 不把“取得协同目标”写成“每次都形成有利混放”，不把“拒绝入池”写成“无额外成本的回退”。
- 贡献中的admission指G1/G2对H必要迁移的已测差别，不暗指新W历史候选已有收益。
- 文献句为原文含义的独立转述，没有替换几个词照抄作者段落。

## 待后续定稿的事项

这是一版可读的Introduction，其他章节仍有占位。后续需要将贡献与最终方法、完整外部对照和负载范围重新对齐；SWEEP全文补读后再判断是否需要纳入引言定位。最终收益数据不从未完成或其他版本的实验拼接。现有标题尚未更改。

当前无本地LaTeX/BibTeX编译器，不能声称已经本地编译通过；静态检查只核对命令/花括号、引用键、BibTeX结构和修改范围。需在Overleaf用根目录 `main.tex` 实际编译检查引用与版面。

本次更新只需上传根目录 `main.tex` 和 `references.bib`。`paper/exports/introduction-v1-20261109.zip`含这两个文件，是当前快照；替换前若网页有新增编辑，先下载并合并。没有直接写入Overleaf，也未改模板样式、实验代码或数据。
