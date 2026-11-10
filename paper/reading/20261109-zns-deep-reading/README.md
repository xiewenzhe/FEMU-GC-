# 新近 ZNS 工作全文精读（2026-11-09）

本批按用户要求，阅读全文后重建论证链，不以摘要或分节梗概代替理解。研究方向仍以本项目计划书为准，本轮不写英文正文、不改算法、不跑收益实验。

## 已完成范围

|论文|版本与全文|本次覆盖|精读笔记|
|---|---|---|---|
|ColdMap: Compaction-Aware Cost-Benefit Zone Cleaning for ZNS-Based Key-Value Stores|ICS 2026，14 页，[DOI](https://doi.org/10.1145/3797905.3807851)；[本地原文](../../../research/rrzc/baselines/materials/ColdMap_Compaction_Aware_Cost_Benefit_Zone_Cleaning_for_ZNS-Based_Key-Value_Stores.pdf)|PDF 第1–14页：正文、公式、全部图表说明、结论和参考文献页；另核对关键原页图表和作者固定源码的若干函数|[ColdMap 全文逻辑](notes/ColdMap-全文逻辑.md)|
|Overlapping Aware Data Placement Optimizations for LSM Tree-Based Store on ZNS SSDs|TACO 22(2), Article 57，2025，25 页，[DOI](https://doi.org/10.1145/3721287)；[本地原文](../../../research/rrzc/baselines/materials/oaza-gc.pdf)|PDF 第1–25页：正文、公式1–4、算法1–3、图1–18、结论和参考文献页；核对关键原页图表|[OAZA/OAZA-GC 全文逻辑](notes/OAZA-OAZA-GC-全文逻辑.md)|
|ZARC: Re-inventing Zone Allocation for LSMs on ZNS SSD|DaMoN 2026，5 页，[作者全文](https://subhadeep.net/assets/fulltext/ZARC_Re-inventing_Zone_Allocation_for_LSMs_on_ZNS_SSD.pdf)、[本地 PDF](sources/zarc-damon2026.pdf)|第1–5页正文、图1–5、表1、Next Steps 和参考文献页；这是 early-stage/vision workshop 论文，不标成完整成熟系统或 A/B 主会论文|[ZARC 全文逻辑](notes/ZARC-全文逻辑.md)|

合计 44 页。ColdMap/OAZA 使用已有本地用户材料，未重复复制 PDF；之前出版方 403 的记录保留，但不再把它们标为缺全文。上一批漏查了 `research/rrzc/baselines/materials/`，此次已核对该目录的来源记录和文件哈希。OAZA 的期刊扩展版与 ASP-DAC 2024 会议版必须分开引用。

没有收到独立 supplementary/appendix 文件，三份 PDF 内没有单列附录；不能据此断言发行方没有其他材料。“参考文献页已读”不代表把其中每一篇也通读或逐条完成元数据核验。作者代码只查关键函数，不声称完成代码审计或复现实验。

## 建议先看

[三篇论文的论证对照与本课题边界](notes/三篇对照与本课题边界.md)把三种决策位置、证据安排及对我们写作的启发放在一起；然后阅读单篇长笔记中的手算例子、证据对应和未解决问题。

单篇笔记中的“论证重建”是对公开文本的分析，不推断作者未写出的实际研究经历。“教学示例”均是人为构造的手算输入，不是论文实测或本项目结果。

## 第二轮：语言精读

按用户后续要求，进一步返回原文分析措辞、语法、指代和证据语气，见
[语言精读索引](language/README.md)。包含ColdMap和OAZA的24处句段，以及JSA/ATC的5处对照；
另有候选术语表和语言细节说明。代表段落精读不等于对每篇每句话完成语言审校。

## 来源与核验

- `catalog.json`：PDF 路径、SHA256、版本与阅读覆盖。
- `verification.json`：页数/文件身份、本地链接、教学算例、论文工程未改的核验记录。
- ColdMap/OAZA 来源记录：[已有用户材料清单](../../../research/rrzc/baselines/materials/user-papers-20261109-v1.json)。
- ColdMap 源码：[作者仓库](https://github.com/sg20180546/ACM_ICS26_ZNS_COLDMAP)，本地固定提交 `2c101302f3b4c29deecd11de86b0db65466f2fbc`，引用仅限核对过的函数。
- DZR、SWEEP 仍未完成全文阅读，状态见[前一批索引](../20261109-ssd-writing/README.md)；它们不计入本批已读篇数。

这些笔记仅本地保存，不需要上传 Overleaf。`main.tex`、`references.bib` 及模板均保持原样。
