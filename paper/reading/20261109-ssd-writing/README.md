# SSD 论文阅读与写作观察（2026-11-09）

目的：先学习 JSA 和相关存储论文怎样提出问题、解释设计、组织证据，再讨论本课题的英文写作。此次没有起草正文，没有修改 `paper/overleaf/`，没有启动实验。

## 阅读范围

“正文重点阅读”指从全文中阅读引言、主要机制、实验设置、关键结果和局限，并定位相关图表；不表示逐条核验参考文献、复现实验或完整审计算法。PDF 页码从文件第一页算起，可能包含会议封面。期刊发表年份以所获版本为依据。

|论文|用途|此次可用材料与覆盖|
|---|---|---|
|[μScope: Evaluating storage stack robustness against SSD’s latency variation](https://zhangbuzhang.github.io/papers/JSA25.pdf)，JSA 2025|近期 JSA、系统观测与因果解释|15 页 PDF 已保存；正文重点阅读 §1、§3–7|
|[ADAPT: An auxiliary storage data path toolkit](https://www.cs.uni.edu/~diesburg/misc/jsa2020.pdf)，JSA，2020 在线版|JSA、跨层架构与实现边界|12 页 PDF 已保存；正文重点阅读 §1–7；所得版本标为 Article in Press|
|[A comprehensive study of energy efficiency and performance of flash-based SSD](https://www.cse.psu.edu/~buu1/papers/ps/park-jsa11.pdf)，JSA 2011|JSA、机制与系统级指标的分层验证|12 页 PDF 已保存；重点阅读 §1、§3–5；历史硬件研究|
|[FineRR-ZNS: Enabling Fine-Granularity Read Refreshing for ZNS SSDs](https://jiemingyin.github.io/docs/DAC2025.pdf)，DAC 2025|直接相关工作、CCF A 类样例|7 页 PDF 已保存；正文重点阅读 §I、III–V、VII|
|[ZNS: Avoiding the Block Interface Tax for Flash-based SSDs](https://www.usenix.org/conference/atc21/presentation/bjorling)，USENIX ATC 2021|ZNS 基础、CCF A 类样例|16 页 PDF 已保存；重点阅读 §1–5 和结论|
|[Design Tradeoffs for SSD Reliability](https://www.usenix.org/conference/fast19/presentation/kim-bryan)，FAST 2019|可靠性维护的代价与收益、CCF A 类样例|15 页 PDF 已保存；正文重点阅读 §1–7，尤其 §4.3–4.5、§5|
|[Ensuring Compaction and Zone Cleaning Efficiency through Same-Zone Compaction in ZNS Key-Value Store](https://www.msstconference.org/MSST-history/2024/Papers/msst24-9.2.pdf)，MSST 2024|直接相关工作、CCF B 类样例|网页可读 14 页全文；重点阅读 §I、III、V–VII；本地下载 TLS 失败，未保存 PDF|
|[DZR](https://doi.org/10.1016/j.sysarc.2024.103159)，JSA 2024|高相关补读候选|出版方预览和[作者代码仓库](https://github.com/hyu-splab/DZR-rocksdb)；尚未获得完整正文|
|[SWEEP: Gathering Hot Read/Write Data Together to Minimize Read Reclaims in SSDs](https://doi.org/10.1145/3795519)，TODAES 2026|共置思想的新颖性边界|检索可见出版方部分正文；PDF 请求 403；尚未完成全文阅读|
|[ColdMap: Compaction-Aware Cost-Benefit Zone Cleaning for ZNS-Based Key-Value Stores](https://doi.org/10.1145/3797905.3807851)，ICS 2026|高相关工作|更新：已找到本地用户全文并完成14页阅读，见[新一批精读](../20261109-zns-deep-reading/notes/ColdMap-全文逻辑.md)；此前403为历史请求状态|

这批样例同时覆盖 JSA、ZNS、RR、放置与清理，但不是穷尽式相关工作综述。ADAPT 是 SSD 相关的跨层存储论文，不是专门研究 ZNS；2011 年论文只用于学习论证组织，不能代替现代 SSD 的现状证据。

后续更新：已补读 ColdMap、OAZA/OAZA-GC 和 ZARC 的共44页全文，见[全文精读索引](../20261109-zns-deep-reading/README.md)。ColdMap先前缺全文的判断漏查了本地基线材料，此处已修正；DZR、SWEEP仍待全文。

## 阅读产物

- [JSA 写作观察](notes/JSA写作观察.md)：三篇文章各自怎样建立论证。
- [相关工作与 A/B 类样例](notes/相关工作与AB类样例.md)：FineRR-ZNS、ZNS、FAST、MSST 的写法，以及未读全文项。
- [对本课题的写作借鉴](notes/对本课题的写作借鉴.md)：建议、证据要求和暂不能下的结论；不是论文正文。
- `catalog.json`：当前成功下载文件的页数、SHA256、来源与覆盖范围。
- `sources/`：6 份论文 PDF，以及一次误返回的 HTML 错误证据。

`download-manifest.json` 和 `additional-download-manifest.json` 是下载尝试记录，其中失败项不代表重试后的最终状态；最终文件状态看 `catalog.json`。不重复长期保存提取文本和版面预览。

## CCF 类别核对

类别用于选择阅读样例，不表示这些文章获过最佳论文奖，也不把会议类别自动套到 workshop。ATC、FAST、DAC 按 A 类样例，MSST 按 B 类样例选择。可核对 [CCF ATC 条目](https://www.ccf.org.cn/Academic_Evaluation/ARCH_DCP_SS/zgjsjxhtjgjxshy/al/2017-03-13/586364.shtml)、[CCF MSST 条目](https://www.ccf.org.cn/c/2017-03-13/586382.shtml)、[CCF FAST 条目](https://www.ccf.org.cn/c/2017-03-13/586369.shtml)及[计算所对 DAC 的类别说明](https://sklp.ict.ac.cn/xwzx/202606/t20260603_840879.html)。部分 CCF 页面直接访问返回 405，ATC/MSST 的类别通过官方条目的检索摘要核对；此次未取得最新版完整官方目录逐条审计。

## 后续使用约定

先补齐最相关论文的全文，再冻结“已有工作—本课题差别—需要什么证据”的对照。不会据标题替作者补全算法，也不会因已建模板就提前填写贡献和性能数字。上述阅读材料不需要上传 Overleaf；正式引用经核对后再加入论文文献库。
