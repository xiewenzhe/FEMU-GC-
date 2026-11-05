# GC 与 RR 操作过程示意

`gc_rr_process.png` 用于解释本课题中 GC（Zone Cleaning）与 RR（Read Reclaim）的相似操作。图片通过内置 image_gen 工具生成；完整生成及修订提示词见 `prompt.txt`。

两者触发条件不同，但共享有效数据迁移路径：确定源区、读取有效数据、写入目标区、更新并持久化映射，在安全条件满足后重置源区。图中块布局仅为概念示意，不代表实测数据。

项目依据（路径相对仓库根目录）：

- `research/rrzc/zenfs-rr-prototype.patch`：GCWorker 与 ReadReclaimOneZone 均调用 MigrateExtents；后者调用 MigrateFileExtents，并通过 ResetUnusedIOZones 尝试回收源区。此文件是原型补丁，不代表所有后续安全检查与严格 RR 的完整实现。
- `research/rrzc/README.md`：开头的维护语义审查确认 GC/ZC 与 RR 共享迁移函数；“2026-10-22：按Block自身触发身份实现严格RR并重新验证”说明严格 RR 的触发、同步迁移及安全失败语义。
- `hw/femu/zns/zftl.c`：读取路径按物理块累计读计数、严格模式报告触发或拒绝继续读取；Zone Reset 路径清除对应块读计数。

范围：仅展示正常成功路径；未展开并发退役、旧读句柄延迟释放、目标预留与错误处理。不能据此认为任意一次复制结束都会立即成功 Reset。

底部协同表述是课题动机，实际是否减少总迁移取决于选区、数据存续和后续维护，需实验验证。未新增性能结果，未修改运行代码，未启动实验。
