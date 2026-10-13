# 面向 ZNS SSD 的 RR 感知 Zone Cleaning：阶段进展记录

截至2026-10-13，50/50与80/20两组各90分钟的隔离配对实验均已完成。
剔除600秒预热后，50/50总维护搬运为8192/8880MiB，80/20为
8192/8976MiB（每组均按有效量baseline/新机制顺序列出）。RR明显减少，
但总搬运未降低；当前仍在分析重复搬运原因，尚未实现新的尾部管理或
迁移成本护栏。后文按实际推进过程保留历史记录，不把早期状态当作最新结论。

目前已完成 FEMU 物理块读取计数、ZenFS 只读遥测接口，以及默认关闭的主机端
RR 原型。**尚未**实现正式 RR-aware ZC 选区模型，也没有模拟 NAND 位错误。
已增加默认关闭、仅用于隔离对照的首次指定选区入口；它不是最终策略，
默认仍使用原批量空间规则，见下文等预算选区对照。
随后在新隔离子镜像接入默认关闭的首版自动成本比较入口，真实
短测通过；完整配对负载因审批服务429尚未执行。该版本不是
最终预测器，见下文自动机制记录。
最新按用户要求切换到更简单的“有效量/最大块读数各50%”版本，
对照为用户确认的每轮单区、只看有效量baseline，不使用趋势或
未来预测。真实完整对照RR次数为1/0，总搬运均128 MiB；详见
下文50/50实验。此前复杂版本不继续作为本轮主线，历史保留。
FEMU 默认 `zns_rr_threshold=0`，不统计读取次数；ZenFS 仅在进程环境变量
`ZENFS_RR_ENABLE=1` 时执行主机端 RR 检查。

当前第二阶段的集中结论、可用输入和未确认问题，见
[第二阶段模型输入整理](#第二阶段模型输入整理)。历史实验记录保留在下文。
最新完成同失效计划下的人工配对选区：本轮热区优先总搬运64 MiB、
冷区优先128 MiB，终态空闲分别704/640 MiB。它是有限受控场景的
真实差异，尚非最终模型、原批量策略或性能对照，详见下文失效实验。

## 代码位置

- FEMU 的 `hw/femu/zns/zftl.c`：读取请求中每个映射有效的4 KiB逻辑页
  增加对应物理块的计数；不是按真实NAND物理页感测次数校准的计数。
  重置 Zone 时清零对应物理块的计数。
- FEMU 的 `hw/femu/zns/zns.c`：按 Zone 汇总物理块计数，通过命名空间专属的
  厂商自定义日志页 `0xc1` 对主机提供数据。
- FEMU 的 `hw/femu/nvme.h` 和 `hw/femu/femu.c`：定义日志页格式及 QEMU 参数。
- 客机中的 ZenFS 位于 `/home/femu/rocksdb/plugin/zenfs`。补丁
  [`zenfs-telemetry.patch`](zenfs-telemetry.patch) 添加只读的
  `ZonedBlockDevice::GetZoneReadPressure()` 接口；完整原型补丁见
  [`zenfs-rr-prototype.patch`](zenfs-rr-prototype.patch)。补丁基于 ZenFS 提交
  `919c2ebbcdc170525a9abffb8b61a3795b1e6ae5`，已同步到隔离客机的写时复制镜像。
- ZenFS 的 `fs/fs_zenfs.cc`：每 10 秒检查一次达到阈值的 Zone；只迁移快照
  完整覆盖、且有效数据全部属于 `.sst` 的 IO Zone。非满 Zone 在独占并复查
  写指针后先封闭，再迁移数据；只处理一个候选 Zone，迁移成功且 Zone 真正
  重置后才计为一次 RR。默认 GC 策略不变。
- ZenFS 的 `fs/io_zenfs.cc` 与 `fs/fs_zenfs.cc`：检查迁移读写结果并向上传递
  错误，避免失败时继续把源 Zone 当作已安全清理。
- ZenFS 的 `fs/fs_zenfs.cc/.h`：在不改变原有 ZC 选区条件的前提下，记录
  ZC 候选 Zone 的无效比例、有效字节和 C1h 读压力；分别统计 ZC 与 RR 的
  尝试次数、实际迁移写入字节和被选源 Zone 的重置数。统计为进程内累计值，
  不是持久计数，也不等同于设备的全部物理擦除次数。
- 分配诊断补丁见
  [`zenfs-allocation-diagnostics.patch`](zenfs-allocation-diagnostics.patch)，
  在上述完整原型补丁之后应用。它修改 `fs/fs_zenfs.cc`、`fs/zbd_zenfs.cc`
  和 `fs/zbd_zenfs.h`，仅在 `ZENFS_ALLOC_DIAG=1` 时开启额外诊断。
  GC 记录实际空闲比例、原始无效比例门槛、候选及片段数量；分配返回空时
  记录原有扫描观察到的空、满、部分 Zone、剩余容量、寿命匹配及令牌情况。
  诊断没有添加 Zone 锁扫描，没有改变 20% 启动水平或选区比较条件。
  占用中的 Zone 标为未观察，两次扫描和 GC 快照均不是原子全局状态，
  不能把未观察数量当成空 Zone 数。原候选日志中的“阈值”改为“RR读数阈值”，
  与新增的 GC 无效比例门槛明确区分。
- 分配回退补丁见 [`zenfs-allocation-fallback.patch`](zenfs-allocation-fallback.patch)，
  在分配诊断补丁之后应用。只修改 `fs/zbd_zenfs.cc/.h`，默认关闭；
  `ZENFS_ALLOC_FALLBACK=1` 时，非 WAL 请求找不到空 Zone、归还新活跃
  令牌后，再次扫描部分 Zone。仅接受寿命匹配差不大于 50 的目标，重新
  取得独占权；部分 Zone 使用自身已有活跃令牌，不再取新令牌。
  最终版本有界重试 200 次、间隔 50 毫秒，等待时不持有 Zone 独占权或
  额外活跃令牌；若 GC 产生新空 Zone，则在取得活跃令牌后可分配。
  最多等待约 10 秒加扫描开销，超时仍保留原分配失败路径，不保证所有
  负载均可运行，也不关闭 RocksDB 空间保护。
  开启回退时，最终失败日志中的扫描明细仍来自进入回退前的原始扫描，
  可能早于打印时间约 10 秒，不能视为重试结束瞬间的全局快照。
  不修改 GC 20% 门槛或 victim 规则，但开启后改变分配行为，须标为
  “分配修复版”，不能称为原始 ZenFS 分配基线。取消环境变量即可回退。

- 维护诊断补丁见
  [`zenfs-maintenance-diagnostics.patch`](zenfs-maintenance-diagnostics.patch)，
  在预留尾部保护补丁后应用。仅 `ZENFS_MAINT_DIAG=1` 时输出成功/失败
  Reset、进程内代际、观测窗口和 SST 覆盖资格；不改 C1h 或磁盘格式。
  默认关闭额外采集与日志，Reset 路径仍有少量原子计数开销。

## C1h 日志页格式：版本 1

32 字节的小端序头部包含 `version=1`、`entry_size=40`、`nr_zones`、
`blocks_per_zone` 和 `read_threshold`。其后按命名空间内的 Zone 顺序排列
40 字节条目。每个条目包含 `zslba`、物理块读取次数的最大值与总和，以及达到
阈值 80%、90%、100% 的物理块数量。阈值关闭时，后三项均为零。

这些值是读取日志页时的快照，并发 I/O 可能继续改变计数。日志页提供根据
物理块计数计算的 Zone 汇总值，**不提供每个物理块的原始计数向量**。
这是 FEMU 专用的研究接口，不是标准 ZNS 接口。

## 已完成的验证

- FEMU 修改后执行 `make -C build-femu -j8`，编译通过。
- 使用 `zns_rr_threshold=4` 启动隔离客机；C1h 返回版本 1、16 个 Zone、
  每个 Zone 64 个物理块，以及阈值 4。
- 在测试 Zone 上执行一次 4 KiB 直接读取后，该 Zone 的最大值与总和均增加 1；
  精确重置该 Zone 后，两者均归零。此前的非零计数包含客机或设备活动，
  不能视为独立负载的测量结果。
- 初始 4 GiB、`zns_num_blk=32` 配置只有 16 个 Zone，ZenFS `mkfs` 返回
  `To few zones on zoned backend (32 required)`，未写入设备。仅增加容量仍保持
  16 个 Zone；最终采用用户选择的 8 GiB 容量，并将 `zns_num_blk` 设为 64，
  实测为 32 个 Zone、每 Zone 256 MiB、64 个物理块。
- 测试时设置 `zns_rr_threshold=16`。在测试子镜像中执行
  `sudo plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path=/tmp/zenfs --enable_gc`
  成功，`zenfs df` 可读取状态；客机将该设备的 I/O 调度器临时设为 `mq-deadline`。
  小规模 RocksDB 基线顺序写入 1000 条 4 KiB 值后，1000 次随机读取全部命中。
- ZenFS 使用与客机现有 RocksDB 对象一致的静态库及无 RTTI 配置编译并链接
  `db_bench`，命令为
  `make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench`。
  默认关闭 RR 时，对已有数据库再读 100 次，全部命中。
- 用 70000 条、每条 4 KiB、禁用压缩的顺序写入建立测试数据库。开启 RR 后，
  15 秒随机读取完成 77999 次，全部命中。原始 ZenFS 日志见
  [`results/zenfs_rr_smoke_2026-10-10.log`](results/zenfs_rr_smoke_2026-10-10.log)。
  日志记录：Zone `1879048192` 有 64 个物理块达阈值，迁移前有效数据
  `66807722` 字节，封闭剩余容量 `135171584` 字节，随后完成一次 RR。
  设备报告该 Zone 已变为 `EMPTY`，C1h 对应条目的读计数归零；之后再次
  随机读取 1000 次，全部命中。最后一处文件身份检查改动重新编译后，
  又读取 100 次并全部命中。这些只是功能烟雾测试，**不是性能对比结论**。
- 增加分项统计后，在同一隔离客机重新编译 `db_bench` 成功。RR 关闭时对
  已有库读取 100 次，100 次全部命中；随后 RR 开启的 15 秒短测完成
  66999 次读取，66999 次全部命中。日志记录一次 RR：Zone `2147483648`
  迁移 `66795251` 字节，并重置 1 个源 Zone。原始日志见
  [`results/zenfs_observability_smoke_2026-10-10.log`](results/zenfs_observability_smoke_2026-10-10.log)。
  这两次读取不是相同初始设备状态下的对照，不能据此比较性能。当前空闲空间
  尚未触发 ZC，因此当时**尚未验证 ZC 候选与迁移统计的运行输出**。对源 Zone
  重置数的最后一处边界修正再次编译通过，随后 100 次读取全部命中；该修正后
  尚未重新触发 RR。

## 2026-10-10：ZC 工作负载预试

以下均为功能与负载可行性预试，**不是 ZC-only 与 ZC＋RR 的有效性能对照**。
实验使用独立写时复制子镜像，FEMU 配置为 8 GiB、32 个 Zone、每 Zone
256 MiB，测试阈值为 16；未修改原始镜像。原始日志保存在 `results/`。

- 7 万条 4 KiB 值的数据库，随后限速覆盖写 30 万次（约 118 秒），没有
  ZC 候选或迁移；日志见
  [`zenfs_zc_small_db_control_2026-10-10.log`](results/zenfs_zc_small_db_control_2026-10-10.log)。
- 保持 RocksDB 默认的 64 MiB `write_buffer_size` 和
  `target_file_size_base` 时，80 万条填充在约 45 万条处报
  `Zone allocation failure`；日志见
  [`zenfs_large_fill_nospace_2026-10-10.log`](results/zenfs_large_fill_nospace_2026-10-10.log)。
  将填充限制到 40 万条并限速后能够完成，但当时没有 ZC 迁移；日志见
  [`zenfs_limited_fill_no_gc_2026-10-10.log`](results/zenfs_limited_fill_no_gc_2026-10-10.log)。
- 调大 `write_buffer_size` 至 512 MiB、`target_file_size_base` 至
  256 MiB 后，15 万条填充成功，并通过 Zone 报告观察到至少一个完整 IO
  Zone；日志见
  [`zenfs_sst_256m_fill_2026-10-10.log`](results/zenfs_sst_256m_fill_2026-10-10.log)。
- 在同一预试设备另建两个各 60 万条的数据库后，对其中一个执行限速覆盖写。
  第一次命令漏写 `--use_existing_db=1`；`db_bench --help` 确认该参数默认
  为 false，已有目标数据库被重新建立，所以这次运行作废；原始日志见
  [`zenfs_sst_wrong_db_open_2026-10-10.log`](results/zenfs_sst_wrong_db_open_2026-10-10.log)。
  重建目标数据库后，带 `--use_existing_db=1` 的覆盖写在约 25 万次操作后
  报 `Zone allocation failure`。维护线程已经输出 ZC 候选，但所列完整
  Zone 的无效比例均为 0%，全部未选中，也没有 ZC 迁移记录；原始日志见
  [`zenfs_sst_existing_db_nospace_2026-10-10.log`](results/zenfs_sst_existing_db_nospace_2026-10-10.log)。

上述早期负载没有稳定地产生“已满且部分无效”的可清理 Zone。
从 ZenFS 代码可确认：`GCWorker()` 仅检查 `capacity == 0` 的 Zone，且
还要求无效比例超过随空闲空间变化的阈值。因此不能仅凭空间不足或出现
候选日志就声称 ZC 已经生效。阈值 16 只是功能压力测试值，
不能作为 QLC 合理阈值或最终性能参数。

## 2026-10-10：首次触发真实 ZC 的负载

仍使用 8 GiB、32 个 Zone、每 Zone 256 MiB 的 FEMU ZNS；此处**仅改变
预试 VM 的 `zns_max_active` 参数**，没有修改 FEMU 默认值或 ZenFS 选区代码。
`zns_max_active=0`（不限）时，128 MiB SST 的 40 万条填充留下许多未满
Zone，原始日志见
[`zenfs_128m_unlimited_active_2026-10-10.log`](results/zenfs_128m_unlimited_active_2026-10-10.log)。
`zns_max_active=16` 时，25 万条填充和 30 万次覆盖写成功，但随后另一个
数据库的 45 万条填充在约 40 万条处报 `Zone allocation failure`；失败后
`zenfs df` 仍显示空闲 2297 MiB，说明剩余可写容量并不保证能分配到新区。
该轮的原始日志见
[`zenfs_active16_static_nospace_2026-10-10.log`](results/zenfs_active16_static_nospace_2026-10-10.log)。

最后在**新格式化的独立易失性设备**上设置 `zns_max_active=8`，
`write_buffer_size=67108864`、`target_file_size_base=67108864`、
`level0_file_num_compaction_trigger=2`、`disable_wal=1`、无压缩、
直接写入、`seed=12345`。按以下顺序执行；每个覆盖写命令均显式带
`--use_existing_db=1`，所有值为 4096 字节：

| 阶段 | 数据库路径 | 操作数 | 写速率限制 | 原始 ZenFS 日志 |
| --- | --- | ---: | ---: | --- |
| 填充 | `/rrzc-active8-hot` | 250000 | 50 MiB/s | [`填充`](results/zenfs_active8_fill250k_2026-10-10.log) |
| 覆盖写 | `/rrzc-active8-hot` | 300000 | 10 MiB/s | [`第一次覆盖写`](results/zenfs_active8_overwrite300k_first_2026-10-10.log) |
| 填充 | `/rrzc-active8-static` | 450000 | 10 MiB/s | [`静态填充一`](results/zenfs_active8_static450k_2026-10-10.log) |
| 覆盖写 | `/rrzc-active8-hot` | 300000 | 10 MiB/s | [`第二次覆盖写`](results/zenfs_active8_overwrite300k_second_2026-10-10.log) |
| 填充 | `/rrzc-active8-static2` | 400000 | 10 MiB/s | [`静态填充二`](results/zenfs_active8_static400k_2026-10-10.log) |
| 填充 | `/rrzc-active8-static3` | 100000 | 10 MiB/s | [`触发 ZC`](results/zenfs_active8_zc_trigger100k_2026-10-10.log) |

最后一步的原始日志显示，ZenFS 在同一轮选中 3 个无效比例分别为 75%、
75%、76% 的满 Zone，实际迁移 `201700397` 字节，并记录重置 3 个源
Zone。完成后 `zenfs df` 报告空闲 1470 MiB、已用 5072 MiB、可回收
880 MiB；随后对热数据库随机读取 1000 次，1000 次均命中，ZenFS 日志见
[`zenfs_active8_post_zc_read_2026-10-10.log`](results/zenfs_active8_post_zc_read_2026-10-10.log)。
这只是一台 VM 的一次功能预试，**尚未证明负载可重复，也不是 RR 的效果**。
迁移后的源 Zone 可能立即被后续写入复用，不能依据测试结束时的 Zone 状态
要求它仍是 `EMPTY`。下一步要从相同空白配置重跑 ZC-only，重复成功后再做
仅在测量阶段启用 RR 的配对预试。

### ZC-only 重复性检查

为保证后续两组命令一致，新增
[`run_qualified_workload.sh`](run_qualified_workload.sh)：脚本只运行
`db_bench` 并保存阶段输出，不负责格式化设备；`rr` 模式也仅在最后阶段
设置 `ZENFS_RR_ENABLE=1`。脚本通过 `bash -n` 检查，复制到隔离 VM 后
又核对了 SHA-256。

在另一块全新格式化的易失性 FEMU ZNS 上，以相同的
`zns_max_active=8` 及六段命令重跑 `zc` 模式。前五段完成，但第五段后
`zenfs df` 为：空闲 1242 MiB、已用 4676 MiB、可回收 1456 MiB。
第六段在约 1.5 万次写入后返回
`put error: IO error: No space left on device: Zone allocation failure`。
该阶段的 ZenFS 日志没有 ZC 候选或迁移记录；进程在第一次 10 秒 GC
检查之前就已经因无法分配新 Zone 而退出。全部原始阶段输出、空间统计与
ZenFS 日志保存在
[`results/qualified-zc-only-run1/`](results/qualified-zc-only-run1/)。
因此上面的一次成功**尚未复现**，不能据此启动 RR 效果对比。现有证据表明
`GetFreeSpace()` 计入活跃 Zone 的剩余容量，但 `AllocateIOZone()` 在寿命
匹配不足时仍可能需要一个空 Zone；两者不保证同步，需先解决触发时机与
可分配空间的错位。用户于 2026-10-11 选择保留原始 20% GC 策略，继续
调整负载；未添加 GC 阈值开关。

## 2026-10-11：单进程负载预试

检查当前代码发现：`Zone` 构造函数将 `lifetime_` 初始化为
`WLTH_NOT_SET`；`ZoneFile::DecodeFrom()` 恢复文件的寿命提示及有效区间，
但没有恢复 Zone 的寿命提示。当前源码对 Zone 寿命的赋值仅在重置或新分配
时发生。因此之前每段单独启动 `db_bench` 的负载会重复挂载 ZenFS，并改变
后续 Zone 的寿命匹配状态。这是已确认的代码行为；它对分配失败的贡献不能
仅凭静态检查量化。

新增 [`run_single_process_pilot.sh`](run_single_process_pilot.sh)，通过当前
RocksDB 已支持的 `fillseq,overwrite[X2],readrandom` 顺序，在一个进程内
填充一次、覆盖写两次、随机读取 1000 次。脚本不格式化设备，不开启 RR；
同一挂载持续运行，GC 门槛仍为 20%。设备沿用 8 GiB、32 Zone、每 Zone
256 MiB、`zns_max_active=8`，写入速率为 20 MiB/s，其余 SST 参数沿用
64 MiB 和 `level0_file_num_compaction_trigger=2`。

90 万条版本在空白设备上完成顺序填充，第一轮覆盖写约 20 万次操作后
报 `Zone allocation failure`。这次 ZC 确实介入：日志选中两个无效比例
约 87% 和 88% 的 Zone，实际迁移 `71944273` 字节，记录重置 1 个源
Zone，随后负载仍失败。失败后 `zenfs df` 显示空闲 966 MiB、已用
4212 MiB、可回收 2172 MiB。原始结果见
[`results/single-process-900k-20261011/`](results/single-process-900k-20261011/)。
该运行不能用于性能对照。随后在重启并重新格式化的设备上，仅将每段数据
规模降到 60 万条：填充和两次覆盖写均成功，1000 次随机读取全部命中，
但日志中没有 ZC 迁移。结束时空闲 3323 MiB、已用 2693 MiB、可回收
1406 MiB；原始结果见
[`results/single-process-600k-20261011/`](results/single-process-600k-20261011/)。
随后测试 75 万条：顺序填充成功，但第一轮覆盖写约 20 万次操作后仍报
`Zone allocation failure`，ZenFS 日志没有实际 ZC 迁移记录。RocksDB 的
原始日志显示 L2 compaction 先分配失败，随后 memtable flush 失败；失败后
空闲 1209 MiB、已用 3507 MiB、可回收 2707 MiB。原始结果见
[`results/single-process-750k-20261011/`](results/single-process-750k-20261011/)。

在新的空白设备上恢复每段 60 万条，仅把 compaction 的 SST 目标从
64 MiB 降到 16 MiB，memtable 仍为 64 MiB、写速率仍为 20 MiB/s。
这次出现 93%、94% 无效比例的候选，并完成两轮 ZC，累计迁移
`572335329` 字节、重置 11 个源 Zone。但负载仍在第一轮覆盖写中失败：
第二轮 ZC 开始于 `16:36:31.091758`，memtable flush 在
`16:36:32.310` 左右分配失败，ZC 直到 `16:36:35.379754` 才完成。
这些时间均为客机原始日志的 UTC 时间。失败并完成清理后空闲空间恢复，
不能据此认定负载成功。原始结果见
[`results/single-process-600k-16m-20261011/`](results/single-process-600k-16m-20261011/)。

据此，下一轮只把写入速率降到 10 MiB/s，保留 60 万条、16 MiB SST 及
原始 GC 策略，检验降低写入压力是否允许清理在分配失败之前完成。
脚本新增可选的第三参数（SST 目标字节数）和第四参数（写速率字节每秒），
省略时仍为 64 MiB SST、20 MiB/s；不改变 ZenFS 或 FEMU 核心实现。

10 MiB/s 版本仍在第一轮覆盖写中失败。其 flush 分配错误发生于
`16:48:44.324123`，GC 的候选快照记录于 `16:48:44.639279`，实际 ZC
完成于 `16:48:46.516401`；迁移 `118058964` 字节、重置 7 个源 Zone。
此次错误早于 GC 候选快照，不能把清理后的恢复空间当作负载成功。
失败并结束清理后空闲 2944 MiB、已用 2750 MiB、可回收 1714 MiB；
原始结果见
[`results/single-process-600k-16m-rate10-20261011/`](results/single-process-600k-16m-rate10-20261011/)。

已核对当前 RocksDB `tools/db_bench_tool.cc` 的
`rate_limiter_bytes_per_sec`：默认值为 0，非零时创建写入限速器；
`file/writable_file_writer.cc` 的直接写路径会请求写入令牌。
与 `benchmark_write_rate_limit` 的前台操作限速不同，这可约束后台
flush/compaction 的文件写入，不限制 ZenFS 自身迁移。
脚本新增可选第五参数，默认仍为 0（不启用）；下一轮仅增加
32 MiB/s 后台写入限速，其他参数不变。这是负载调节，不是 GC 策略修改。

32 MiB/s 后台限速版本在第一轮覆盖写超过 50 万次后仍失败。
RocksDB 日志记录非空的 `Options.rate_limiter`，失败 compaction 的
原始吞吐字段为 `MB/sec: 29.4 rd, 23.5 wr`。首次 compaction
分配失败在 `17:00:16.867` 左右，GC 候选记录于 `17:00:18.080038`，
ZC 完成于 `17:00:18.563630`；迁移 `29782858` 字节、重置 2 个源 Zone。
失败并清理后空闲 1805 MiB、已用 4115 MiB、可回收 1479 MiB。
原始输出、数据库日志、ZenFS 日志及**清理后而非错误瞬间**的 Zone 状态见
[`results/single-process-600k-16m-bg32-20261011/`](results/single-process-600k-16m-bg32-20261011/)。

仅通过前台或后台限速尚未获得可完成的 ZC 负载。下一轮保持后台限速及
其余参数，仅把每段规模从 60 万条降到 55 万条，检验降低容量压力是否
仍能触发 ZC 并完成负载；没有改变默认 GC 门槛或选区条件。

55 万条版本完成顺序填充及第一轮覆盖写，但第二轮覆盖写超过 10 万次后
分配失败。`17:11:55.933382` 完成一轮 ZC，迁移 `16865566` 字节、重置
1 个源 Zone；下一次检查 `17:12:05.934` 的满 Zone 候选最高无效比例为
76%，全部记录为“选中 0”。约 `17:12:06.314` compaction 分配失败。
失败后空闲 1440 MiB、已用 4180 MiB、可回收 1803 MiB；原始结果见
[`results/single-process-550k-16m-bg32-20261011/`](results/single-process-550k-16m-bg32-20261011/)。
这说明失败并非都发生在首次 GC 检查之前；也可能检查已经执行，但没有
满足原始选区条件的候选。现有日志中的“阈值 16”指 **RR 读数阈值**，
不是 GC 的无效比例门槛，不能混用。

当前结论：单进程避免了重复挂载这一额外因素，小 SST 可以产生实际 ZC，
但已测试的限速和容量调节尚未得到“负载完成且触发 ZC”的可重复组合。
60 万条、64 MiB SST 的一次完整成功没有 ZC，不能替代所需基线。
尚未开始 RR 配对效果实验，也没有形成 RR-aware victim 策略。
下一步优先补充 GC 检查及分配失败时的空 Zone 数、部分 Zone 剩余容量、
`free_percent` 和 GC 无效比例门槛观测，仍保留原始 20% 策略，再据实际
状态调整负载。不把清理后的 Zone 状态当作错误瞬间状态。

## 2026-10-11：GC 与 Zone 分配诊断

新增默认关闭的 `ZENFS_ALLOC_DIAG=1` 日志开关，只观察、不改变选区和分配
条件。三个目标源文件在修改前与已运行客机的 SHA-256 一致；独立补丁在
新子镜像中先执行 `git apply --check`，再应用。原有未提交修改保留。
诊断子镜像为 `/home/data/xwz/rrzc-baselines/allocation-observe-20261011.qcow2`，
基于已停止的 `zc-only.qcow2` 创建，未修改原始镜像或之前的实验子镜像。

客机执行
`make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench`
成功，编译日志见
[`results/allocation-diagnostics-20261011/build.log`](results/allocation-diagnostics-20261011/build.log)。
新二进制 SHA-256 为
`cc870ec02e1daf924b622d945c0ef378eae0a91d59814aa7c718f4a441e1feef`。
宿主补丁格式检查、逆向应用检查和负载脚本语法检查通过；逆向检查只验证
可回退，没有实际撤销补丁。

先在新空白设备做最小测试：诊断关闭时填充 1000 条，随后 1000 次随机
读取全部命中，两个 ZenFS 日志均没有新增的诊断输出。开启诊断后进行
12 秒读取，日志记录 `GC=1`、`主机端 RR=0`，在第 10 秒检查时空闲比例
99%、启动水平 20%、不进入选区；读取全部命中。这是开关和读写功能检查，
不是性能测量或完整数据校验。原始输出及三个 ZenFS 日志见
[`results/allocation-diagnostics-20261011/smoke/`](results/allocation-diagnostics-20261011/smoke/)。

最小测试日志保存后关闭 VM，并重新启动以清空易失性 ZNS。下一轮使用
`ZENFS_ALLOC_DIAG=1` 重跑每段 55 万条、16 MiB SST、64 MiB memtable、
前台 10 MiB/s、后台 32 MiB/s 的单进程负载；设备仍为 8 GiB、32 Zone、
`zns_max_active=8`、计数测试阈值 16，原始 GC 启动水平为 20%，RR 关闭。

### 诊断重跑的实际结果

填充 55 万条及第一轮覆盖写 55 万次均完成，第二轮覆盖写超过 20 万次后
返回 `put error: IO error: No space left on device: Zone allocation failure`。
本轮实际完成一次 ZC，迁移 `31970078` 字节、重置 2 个源 Zone。
负载没有完整完成，不能作为可重复性能基线。原始负载输出、ZenFS 日志、
RocksDB 日志、失败后空间与 Zone 状态、源码和二进制身份见
[`results/allocation-diagnostics-20261011/run1/`](results/allocation-diagnostics-20261011/run1/)。
重跑脚本 SHA-256 与宿主脚本一致，未在本轮改变负载命令。

日志给出以下直接证据，时间均为客机原始 UTC 时间：

- `02:11:35.357109`：空闲 19%，无效比例门槛 97%，候选最高 95%，
  没有选中 Zone。`02:11:45.357902`：空闲 15%，门槛降到 85%，选中
  2 个 Zone，随后 `02:11:46.024570` 完成上述 ZC。
- `02:11:56.025205`：空闲 17%，门槛 91%，候选最高 76%，选中 0；
  快照没有空 IO Zone，6 个部分 Zone 合计剩余 `1354314240` 字节。
  随后长寿命文件的两次分配扫描记录匹配差 100、放弃已有匹配、未获得
  空 Zone。此时出现后台 compaction 错误，前台负载随后仍继续运行；
  不能把首次“分配返回空”直接当作最终进程退出时刻。
- `02:12:45.865161` 与 `02:12:46.664882`：中等寿命文件的匹配差为 50，
  原有扫描找到部分 Zone，但取得活跃令牌后放弃匹配，转而寻找空 Zone，
  最终未获得空 Zone。扫描观察到 28 个 IO Zone，其中空 0、满 24、部分
  4，部分剩余合计 `749645312` 字节；另有 1 个占用中的 Zone 未观察。
  寿命值 3 为 `WLTH_MEDIUM`，4 为 `WLTH_LONG`；**匹配差 50 在当前
  `GetLifeTimeDiff()` 中也包含相同寿命提示，并非一定是寿命不匹配**。
  `IO类型=7` 是当前 RocksDB 的 `kUnknown`，不能仅凭此字段认定是 flush
  或 compaction；RocksDB 日志分别记录了这两种路径的错误。
- `02:12:46.028867`：空闲 13%，门槛 79%，满 Zone 最高无效比例 78%，
  选中 0；快照中 5 个部分 Zone 剩余 `1017988608` 字节。随后 flush
  分配失败，前台输出上述 `put error`。

本轮证实：统计剩余字节不等于分配器能获得一个空 Zone；即使 GC 已进入
选区，也可能没有达到原始无效比例门槛的候选。当前失败涉及部分 Zone 的
分配规则与 GC 选区门槛共同作用，不只是 10 秒检查间隔或实际容量耗尽。
设备的最大活跃 Zone 数为 8，但 `ZonedBlockDevice::Open()` 预留 2 个
令牌，普通 IO 上限为 6；日志中的 `5/6` 是该分配请求释放令牌后的状态，
不代表获取新令牌前或所有设备 Zone 的状态。

失败并退出后 `zenfs df` 记录空闲 1226 MiB、已用 4427 MiB、可回收
1716 MiB。此时 compaction 失败输出已被删除，Zone 状态与错误瞬间不完全
相同，不能拿它反驳运行时的分配扫描。诊断日志可能扰动并发时序，不能把
与前轮不同的失败进度当作性能收益。下一步依据这些证据调整负载的 Zone
占用和无效数据分布；原始 GC 门槛和分配规则不变，RR-aware 选区机制仍未实现。

## 2026-10-11：减少部分 Zone 占用的单变量预试

上一轮运行时没有空 IO Zone，而多个部分 Zone 的剩余容量仍计入空闲空间。
本轮仅将隔离 VM 的 `zns_max_active` 从 8 改为 4；依照当前
`ZonedBlockDevice::Open()`，普通 IO 活跃令牌上限由 6 变为 2。
这是实验配置预试，不修改 FEMU 默认参数、ZenFS 分配器或 GC 20% 策略，
也不直接确定最终研究设备配置。推断：减少部分 Zone 的占用，可能使低空闲
检查时仍保留可分配的空 Zone；是否有效以真实运行为准。

容量仍为 8 GiB、32 个 256 MiB Zone，每 Zone 的物理组织不变。
负载仍为单进程填充一次、覆盖写两次，各 55 万条，16 MiB SST、64 MiB
memtable、前台 10 MiB/s、后台 32 MiB/s、固定种子 12345；RR 关闭，
诊断开启，计数功能测试阈值仍为 16。新子镜像
`/home/data/xwz/rrzc-baselines/active4-pilot-20261011.qcow2` 基于已停止的
`allocation-observe-20261011.qcow2` 创建，不覆盖此前镜像。
二进制和三个诊断源文件 SHA-256 与上一轮一致，未重新编译。

先做小测试：Identify Namespace 的 `mar=0x3` 表示最大活跃 Zone 数 4，
实际报告 32 个空 Zone、每 Zone 容量 524288 个 512 字节 LBA。
填充 1000 条成功，随后 1000 次随机读取全部命中，没有分配失败；
ZenFS 日志确认 `max active: 4`、`GC=1`、`主机端 RR=0`。
原始配置、Zone 报告和读写日志见
[`results/active4-pilot-20261011/smoke/`](results/active4-pilot-20261011/smoke/)。
保存小测试结果后关闭并重新启动 VM，再在空白设备上重跑完整负载。
这一轮只检验可运行性，不把与最大活跃 Zone 数 8 的差异作为 RR 收益或
公平的性能对比。

### 最大活跃 Zone 数 4 的完整负载结果

填充 55 万条成功，第一轮覆盖写超过 30 万次后因 Zone 分配失败退出。
GC 进入了 4 次迁移尝试，但累计实际迁移字节和源 Zone 重置数均为 0；
每次原始错误均为“没有可用的迁移目标 Zone”。例如 `02:28:16.115400`
检查时空闲 17%、无效比例门槛 91%，选中 1 个候选；快照中尚有 3 个空
IO Zone 和 2 个部分 Zone，迁移仍然失败。这不能称为成功完成 ZC。
退出后空间为：空闲 767 MiB、已用 3343 MiB、可回收 3312 MiB。
原始结果见
[`results/active4-pilot-20261011/run1/`](results/active4-pilot-20261011/run1/)。

代码确认：`TakeMigrateZone()` 只调用 `GetBestOpenZoneMatch()`，不调用
`AllocateEmptyZone()`。匹配要求 Zone 可取得独占权、有有效数据、未满，
剩余容量至少覆盖本次迁移片段。快照有空 Zone 或剩余总量并不保证存在
满足这些条件的目标。现有日志尚不能区分当次目标具体因占用、有效数据
或片段大小哪个条件被排除；不据此编造精确竞争过程。

最大活跃数 4（普通 IO 上限 2）仍不足以支撑这组并发负载的迁移。
下一轮在新的隔离子镜像仅将该参数调为 6（普通 IO 上限 4），其余参数
保持不变，检验减少部分 Zone 占用与保留迁移目标之间是否存在可运行范围。
没有更改分配器、迁移器或 victim 选择条件，最终研究设备配置尚未确定。

### 活跃上限 4 的日志拷贝中断与只读恢复

本轮完整 ZenFS 日志为 `2990021940` 字节。第一次拷贝尚未完成就关闭了
VM，SCP 返回 `lost connection`；该不完整副本已明确命名为
`run1/zenfs.partial.log` 并保留，不能当作完整原始日志。
负载输出、RocksDB 日志及空间结果已完整保存。

随后在新的隔离客机中将此前的实验子镜像作为只读附盘打开，不重新启动旧系统。
带旧盘同时启动时 SSH 未就绪，具体原因尚未确认；改为正常单盘启动后
只读热接旧盘。根据 `pvck --dump metadata` 确认旧根卷是一个连续段，
起点为分区内 2048 个扇区、长度 `41871736832` 字节；用只读 loop 映射，
以 `ro,noload` 挂载 ext4，不修改旧盘 LVM 元数据、内核或 GRUB。
恢复后卸载并释放该只读映射。

完整无损压缩日志、诊断摘录与校验信息保存在
[`results/active4-pilot-20261011/run1/recovered/`](results/active4-pilot-20261011/run1/recovered/)。
宿主 `gzip -t` 通过，解压后的 SHA-256 与旧镜像原文件一致：
`698bfaade31aaf3a26d65bef96d2e6957d883d5ceccfa4aa49e8b5527a5f9569`。
日志中原有 `All non-busy zones are empty or full, skip.` 提示重复
`39338067` 行；对应 `FinishCheapestIOZone()` 无可处理 Zone 时返回成功，
调用侧循环再次尝试。这不是新增诊断字段的高频输出，也不能用这一运行
与其他配置作性能对比。未修改该原有循环或删除原始日志。

### 最大活跃 Zone 数 6 的小测试

新子镜像 `/home/data/xwz/rrzc-baselines/active6-pilot-20261011.qcow2` 同样
基于已停止的诊断镜像创建，最大活跃 Zone 数为 6，普通 IO 上限为 4。
Identify Namespace 报告 `mar=0x5`，1000 条填充成功，随后 1000 次随机
读取全部命中。原始结果见
[`results/active6-pilot-20261011/smoke/`](results/active6-pilot-20261011/smoke/)。
小测试及恢复日志全部复制并校验完成后关闭 VM。下一轮不带旧恢复盘，
从空白易失性 ZNS 上运行与上限 4、8 相同的 55 万条单进程负载，仍保持
GC 20%、RR 关闭和原有二进制。没有通过改代码绕过迁移目标分配条件。

### 最大活跃 Zone 数 6 的完整负载结果

填充 55 万条与第一轮覆盖写 55 万次均完成，第二轮覆盖写超过 25 万次后
因 `Zone allocation failure` 退出，末尾的随机读取未执行。完整输出、
RocksDB 日志、ZenFS 日志、退出后空间和 Zone 报告已保存到
[`results/active6-pilot-20261011/run1/`](results/active6-pilot-20261011/run1/)。
确认拷贝完成后正常关闭隔离 VM，原有 8080 端口的 VM 未关闭。

本轮有两轮实际 ZC：第一轮迁移 `40921974` 字节、重置 1 个源 Zone；
第二轮迁移 `66797145` 字节、重置 1 个源 Zone；累计 `107719119` 字节、
2 个源 Zone 重置。完整 ZenFS 日志没有“ZC 迁移失败”记录，但整体负载
仍未完成，不能称为稳定基线，也不能据此声称 RR 收益。

失败前的直接证据如下，时间为客机原始 UTC 时间：

- `02:55:38.111956`：空闲 9%、无效比例门槛 67%，选中 0 个 Zone。
  该快照中，除一个近似无效比例为 100% 的 Zone 外，满 Zone 的最高
  无效比例为 58%；原有选择条件排除 100%。这个近似 100% 的 Zone
  实际仍有 `7902` 字节有效数据，不能把整数比例当作完全无有效数据。
  快照没有空 IO Zone，4 个部分 Zone 合计剩余 `739504640` 字节。
- `02:55:46.097819` 与 `02:55:46.108115`：分配扫描找到相同中等寿命的
  部分 Zone，匹配差 50；取得活跃令牌后放弃匹配，转去寻找空 Zone，
  但没有获得空 Zone。扫描观察到 28 个 IO Zone，其中满 26、部分 2、
  空 0，部分剩余合计 `203907072` 字节；另有 1 个占用中的 Zone 未观察。
  日志中活跃令牌为请求释放令牌后的 `3/4`，不是获取令牌前的状态。

退出后 `zenfs df` 为：空闲 706 MiB、已用 4597 MiB、可回收 1962 MiB。
它是退出后的状态，不等同于上述失败瞬间的并发扫描。

本轮只改变隔离设备的最大活跃 Zone 数，没有改动源码、默认配置、原始
GC 20% 策略或 victim 选择规则；复用已编译的诊断二进制，没有重新编译。
两个配置均通过 1000 条填充和 1000 次随机读取的小测试，但完整预试均失败。
降低活跃上限不能单独解决这组负载的问题，最终设备配置仍未确定。
建议下一轮固定活跃上限 6，只将每阶段条目数从 55 万减为 50 万，检验
能否在发生实际 ZC 的同时完整运行；这只是待验证方案，尚未执行。
不先修改分配器、迁移目标选择或 GC 门槛来绕过基线失败。

## 2026-10-11：默认关闭的分配回退修复

用户进一步要求定位并解决问题后，依据上述失败扫描，在独立修复子镜像
`/home/data/xwz/rrzc-baselines/allocation-fallback-20261011.qcow2` 中验证
最小分配修复。镜像基于已停止的 `allocation-observe-20261011.qcow2`，
不写入旧基线镜像。保留原始 GC 20%、斜率 3 和 victim 选区条件。
只在 `ZENFS_ALLOC_FALLBACK=1` 时改变找不到空 Zone 后的非 WAL 分配路径，
不修改寿命差函数，不添加空 Zone 迁移目标，也未处理原有 Finish 重试循环。
该试验是分配修复验证，不是原始 ZenFS 或 RR-aware 性能结果。

控制流测试见 [`tests/allocation_fallback_test.cc`](tests/allocation_fallback_test.cc)
及 [`tests/run_allocation_fallback_test.sh`](tests/run_allocation_fallback_test.sh)。
脚本从当前生产源文件机械提取 `AllocateIOZone()`，使用替代的设备和扫描
依赖验证：关闭开关保留原结果、合适部分区回退、正常空区分配、拒绝匹配差
100、回退扫描报错、扫描时目标变得不可用、WAL 不回退，共 7 项通过。
检查了独占权与打开/活跃令牌计数；这些不是 NAND 或并发集成测试。

客机执行
`make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench`
编译成功。SHA-256：

- `db_bench`：`070aa6331622f31829651ef987d914cda4b3463f13127cf5df475b54e7a9ae18`。
- `fs/zbd_zenfs.cc`：`8d2c17882e05e4f76b325f842e5654d57572c2c6a909cc39f6fbf4f0e6fcdcf2`。
- `fs/zbd_zenfs.h`：`208cf187ebc9a279c16ed17f1f1ee6f83da00c94793d669496511b12ed616eca`。

回退开关关闭、开启时分别执行 1000 条填充和 1000 次随机读取，均正常
退出且全部命中。该小库读取可能命中 memtable/cache，不能证明发生设备
迁移；只是启动与读写烟雾测试。原始结果见
[`results/allocation-fallback-20261011/smoke/`](results/allocation-fallback-20261011/smoke/)。
保存全部日志后正常关闭并重启隔离 VM，使易失性 ZNS 为空白。
完整验证保持上限 6、每阶段 55 万条和此前所有负载参数，开启诊断及回退，
关闭 RR。首次启动脚本时发现重启清空 `/tmp`，返回 127，负载未开始；
重新复制原脚本后启动实际负载，脚本 SHA-256 不变。

### 首版回退结果与第二个阻塞原因

首版回退真实成功 8 次，第二轮覆盖写超过 35 万次；累计发生 3 轮 ZC，
迁移 `175441682` 字节、重置 5 个源 Zone。但是 `03:23:31.863899` 的
扫描观察到 27 个满 Zone、2 个占用未观察 Zone，没有可取得的空或部分
Zone，仍返回分配失败。随后 RocksDB 的后台错误触发更严格的空间检查。
原始记录见
[`results/allocation-fallback-20261011/run1/`](results/allocation-fallback-20261011/run1/)。
该轮因持续 compaction 取消和负载停顿，由操作者发出 Ctrl-C 中断，
不是正常完成，也不计算为有效性能样本；首版补丁随结果单独保留。

当前客机 RocksDB `DBImpl::EnoughRoomForCompaction()` 调用
`SstFileManagerImpl::EnoughRoomForCompaction()`。后者在背景错误为
`NoSpace` 且可检查空间时，按当前保留空间、合并输入大小和额外保留缓冲
进行严格空间检查。`03:23:34.172826` 日志记录空闲 `1377464320` 字节，
打印的所需 headroom 为 `2667457811` 字节，合并被取消；代码实际比较
还包含一次输入大小，不能把打印 headroom 当作精确最终门槛。
这解释了为何 ZC 回收后仍持续出现 `Compaction too large`。

依据这个瞬时分配失败到背景空间保护的链路，在同一个默认关闭的回退
开关内添加上述有界重试，不改 RocksDB 检查。控制流测试扩展为 11 项，
增加了短暂占用恢复、等待后出现新空区、活跃令牌已满时保持原行为、
重新分配空区报错。首轮测试中“等待后有空区”替代依赖未设置首次占用，
断言失败；修正测试场景后 11 项通过，并未因此改动生产逻辑。
测试不使用真实等待；结果保留为 `unit-tests-v1.log`、`unit-tests-v2.log`。
有界重试版客机再次编译成功，最终源文件和二进制身份在后续结果中保存。

有界重试版二进制 SHA-256 为
`1bfacba418e63f5c028094978ee18d86fdbfd8d771f94e5e14ed594658e1c1e7`，
`fs/zbd_zenfs.cc` 为
`e350c6a97e66e7437f5640b9ebac74b7cd5e6c5a6106ba5da38c2919ae88b74f`，
头文件与首版一致。首轮中断后，使用有界重试版执行存量库
`verifychecksum`，成功退出；它检查 SST 校验和，不验证中断时尚在
memtable 的数据持久化。另一个 1000 条填充与 1000 次读取的小测试
也成功退出、全部命中。标准输出与身份校验保存在
[`results/allocation-fallback-20261011/smoke-v2/`](results/allocation-fallback-20261011/smoke-v2/)。
首次执行校验命令时未切换到 RocksDB 目录，返回 127；原始报错保留为
`launch-error.stdout.log`，切换目录后才得到上述真实校验结果。
保存结果后正常关闭并重启隔离 VM，再从空白设备启动同参数第二轮完整复测。

### 有界重试版 55 万条结果

第二轮覆盖写超过 50 万次，但未完成；发生了 11 次回退成功，其中一次
在重试 15 次后取得 GC 新回收的空 Zone，另一次重试 158 次后取得空区。
累计 6 次 ZC 迁移尝试：前 4 次实际迁移 `154738238` 字节、重置 4 个
源 Zone；后 2 次因没有可用迁移目标失败，均为 0 字节、0 重置。
`03:43:11.008767` 又有一次分配重试耗尽返回空，随后重复出现
`Compaction too large`，由操作者中断。因此仍不是完整或稳定的基线。
原始结果见
[`results/allocation-fallback-20261011/run2/`](results/allocation-fallback-20261011/run2/)。
末段日志的空闲 `744570368` 字节，小于打印的 compaction headroom
`3186631082` 字节；输出的 headroom 同样不是代码最终比较门槛。
中断后空间为：空闲 710 MiB、已用 5155 MiB、可回收 1558 MiB。
已确认回退和等待机制真实起作用，但不能把它解释为已经解决所有分配和
迁移问题；尤其原迁移器仍要求有足够容量且可独占的部分 Zone。

下一次保持二进制、活跃上限 6、GC 20%、回退开启、RR 关闭和其余负载
参数不变，仅将每阶段条目数从 55 万降为 50 万。这个单变量预试检验是否
能保留足够迁移余量且发生实际 ZC，不再叠加新的核心修改。

### 50 万条完整运行与数据校验

第三轮只减小条目数：50 万条填充、两轮各 50 万次覆盖写和最终 1000 次
随机读取均完成，主脚本退出码为 0，随机读取全部命中。主负载与此前
55 万条试验使用同一个有界重试二进制、同一脚本，其余设备、速率和
RocksDB 参数不变。实际回退成功 8 次，包含使用部分区以及等待 GC
产生新空区的路径。完整主负载 ZenFS 日志没有“Zone 分配返回空”，
保存的主负载 RocksDB 日志没有 `Zone allocation failure`、
`Compaction too large`、`Corruption` 或 `[ERROR]`。

主负载发生 8 次 ZC 迁移尝试，累计迁移 `531528367` 字节、重置 10 个
源 Zone。其中第 7 次先迁移 `42149050` 字节，随后因无可用迁移目标
返回错误，没有重置源 Zone；第 8 次继续迁移并重置 3 个源 Zone。
不能把 8 次尝试全部描述成成功清理，也不能丢弃失败尝试已写入的迁移量。
负载后空间为：空闲 1810 MiB、已用 4161 MiB、可回收 1307 MiB。

随后对存量数据库执行 `verifychecksum,readseq`，关闭 RocksDB 自动
compaction、开启直接读取和校验和检查；校验成功，顺序遍历完成
500000 个键，进程退出码为 0。该验证不证明关闭 WAL 时最后一批
memtable 写入的持久化语义，也不验证每个键的最新值内容。
后校验的 ZenFS 日志与主负载日志分开保存，不合并进上述 ZC 统计。
原始结果、实际退出码、二进制和源码身份、初始/结束 Zone 报告见
[`results/allocation-fallback-20261011/run3-500k/`](results/allocation-fallback-20261011/run3-500k/)。

本轮结论：在保持原始 GC 20% 和 victim 规则的条件下，默认关闭的分配
回退/有界重试修复已通过编译、11 项控制流测试和真实路径验证；配合
50 万条负载，获得了一次完整运行且实际发生 ZC 的候选基线。
这解决了当前寻找可运行负载的直接障碍，但不能说已经修复所有 Zone
分配或迁移问题：55 万条仍失败，50 万条也存在一次非致命 ZC 迁移失败。
截至第三轮结束，尚未重复验证该成功配置，尚未进行 RR 对照或 RR-aware victim 设计。

开启回退的试验必须标为“分配修复版”，不是原始 ZenFS 分配基线；
后续若采用它，对照组和实验组必须使用相同回退配置。是否使用原始分配器
也能稳定运行 50 万条仍未验证。建议下一步先重复本次成功配置，再决定
正式基线配置，不将单次通过或不同负载结果解释为性能收益。

## 2026-10-11：50 万条同参数重复验证未通过

重复验证采用新的子镜像
`/home/data/xwz/rrzc-baselines/repeat-500k-20261011.qcow2`，基于已停止的
`allocation-fallback-20261011.qcow2` 创建，不写入上次成功镜像。
启动前确认没有其他 8089 实验 VM；原有 8080 VM 保持不动。
客机实际报告 32 个空 Zone、每 Zone 256 MiB、最大活跃上限 6；
二进制、三个相关源文件和负载脚本 SHA-256 均与上次成功结果一致。
本轮未修改源码、未重新编译。GC 20%、斜率 3、victim 规则、回退开启、
RR 关闭、50 万条与前后台限速等参数不变。

填充 50 万条和第一轮覆盖写 50 万次完成；第二轮超过 45 万次后停顿，
没有完成末尾随机读取。日志记录 `05:43:27.134719` 和
`05:43:31.138048` 两次分配返回空；对应 RocksDB 日志分别记录 flush
与 compaction 的 `Zone allocation failure`。进入回退前的扫描观察到
没有可取得的部分 Zone，并有占用未观察 Zone；不能把这份扫描当作约
10 秒后重试耗尽瞬间的状态。此后负载未继续推进，GC 重复尝试迁移。

`05:44:27.486732` 快照仍有 2 个空 IO Zone、1 个部分 Zone，空闲 10%，
无效比例门槛 70%，选中 2 个候选及 5 个迁移片段，但仍没有可用迁移
目标。代码确认 `TakeMigrateZone()` 仅调用 `GetBestOpenZoneMatch()`，
不申请空 Zone；匹配要求能取得独占权、有有效数据、未满并可容纳片段。
因此空 Zone 数量或剩余字节不等于该迁移器可使用的目标。
当前没有逐次目标筛选诊断，尚不能精确区分该部分 Zone 被占用、无有效
数据或容量不够的具体原因，不能自行补全。也不能把上轮
`Compaction too large` 的后续行为直接当成本轮已确认行为；本轮确认的是
后台分配错误、写入停顿及反复迁移目标失败。

保留停顿状态的原始日志和 Zone 报告后，操作者发出 Ctrl-C 中断，实际
状态码为 130，不是正常完成。中断后空间为：空闲 767 MiB、已用
4442 MiB、可回收 2213 MiB。主负载截至中断累计 32 次 ZC 迁移尝试，
迁移 `512034730` 字节、重置 9 个源 Zone；回退成功 14 次，分配返回空
2 次。26 次迁移错误包括 1 次迁移期间文件删除的 `NotFound` 和 25 次
没有可用迁移目标；后期失败尝试为 0 字节、0 重置。错误次数受人工等待
时长影响，不作为可比较的性能指标。

中断后对存量库执行与上一轮相同的 `verifychecksum,readseq`，校验通过，
顺序读取 500000 个键，退出码为 0。这只证明存量 SST 的此次检查通过，
不意味着第二轮覆盖写完成，不验证每个键的最新值，也不验证关闭 WAL
时未完成写入的持久化。主负载与后校验日志分开统计。
原始结果、退出状态、身份和 Zone 报告保存在
[`results/repeat-500k-20261011/`](results/repeat-500k-20261011/)。

| 相同配置的 50 万条试验 | 主负载 | ZC 迁移字节 | 源 Zone 重置 | 分配返回空 |
| --- | --- | ---: | ---: | ---: |
| 上一轮 `run3-500k` | 完整完成 | 531528367 | 10 | 0 |
| 本轮 `repeat-500k` | 停顿后人工中断 | 512034730 | 9 | 2 |

结论：同参数一轮成功、一轮未完成，不能认定为稳定基线，也不能据此
开始正式 RR 收益对照。分配回退已经覆盖部分失败路径，但没有解决迁移
目标和可用空间保障。下一步建议先单独研究迁移目标分配及回收空间预留，
继续保持 GC 20% 和 victim 规则不变；另一方案是扩大设备容量、保持
每 Zone 物理组织，再重新选择负载。这两种路径分别涉及核心执行路径或
实验设备配置的变化，需要确认方案后实施；本轮没有擅自添加空 Zone
迁移目标、修改容量或继续叠加补丁。

关键测试命令如下，均在客机 `/home/femu/rocksdb` 下运行；测试数据库路径在
ZenFS 命名空间内，原始日志由上面的链接保留。

```bash
sudo ./db_bench --fs_uri=zenfs://dev:nvme0n1 --benchmarks=fillseq --num=70000 --value_size=4096 --compression_type=none --db=/rrzc-rrtest --use_direct_io_for_flush_and_compaction=1
sudo ZENFS_RR_ENABLE=1 ./db_bench --fs_uri=zenfs://dev:nvme0n1 --benchmarks=readrandom --num=70000 --reads=1000000 --duration=15 --db=/rrzc-rrtest --use_existing_db=1 --use_direct_reads=1 --cache_size=1048576
```

## 2026-10-11：迁移目标与空间预留修复（实验开关）

用户同意优先修复迁移资源，而不是扩大设备或改变 GC 门槛。本轮新增
[`zenfs-migration-reserve.patch`](zenfs-migration-reserve.patch)，应用顺序为
RR 原型、分配诊断、分配回退、迁移预留。当前源码副本位于
`build-femu/rrzc/zenfs-source/fs/`，修改前副本保留在
`build-femu/rrzc/migration-reserve-before/`；客机对应目录为
`/home/femu/rocksdb/plugin/zenfs/fs/`，另保留
`/home/femu/rrzc-migration-reserve-before/`。未修改本轮之外的 FEMU 源码。

新增 `ZENFS_MIGRATE_RESERVE=1`，默认不设置，保持旧分配和迁移分支。
本开关不是 RR-aware victim selection，也不更改 GC 20%、斜率 3、
检查周期或 victim 条件。修改三个文件：

- `zbd_zenfs.h`：实验状态、专用目标指针与预留互斥锁。
- `zbd_zenfs.cc`：写打开时取得专用空 Zone；普通空区分配与回收串行，
  优先补充缺失的预留。`TakeMigrateZone()` 仍优先选择原有部分目标，
  无目标时可使用专用区。专用区跨片段保持 busy，普通分配、Finish 和
  Reset 扫描无法抢占。容量不足时无有效数据则重置，否则 Finish 后尝试
  补充新的空区；没有空区仍可能失败，并未提供无限容量保证。
  `ReleaseMigrateZone()` 关闭目标；普通目标被迁移写满时归还原有活跃
  令牌，专用目标不归还普通令牌。专用区使用原有两个预留配额中的迁移
  配额，另一个仍用于元数据，不增加设备的最大活跃数。重新写挂载时，
  若恢复的普通活跃计数超出普通上限，接管一个已有部分区并从普通计数
  扣除，避免把旧专用区和新专用区重复计数；不是恢复持久化的预留身份。
- `fs_zenfs.cc`：只在实验模式按实际写入字节申请目标容量，包括稀疏头
  与块对齐填充；不开启时继续传原片段长度。

影响与回退：取得预留后有一个 IO Zone 不供普通写入使用，专用部分区
的剩余空间也不会供前台复用；其剩余不足时 Finish 会损失尾部容量。
`GetFreeSpace()` 沿用原有统计，仍包含专用区剩余字节，所以该数值不是
普通写入可分配容量。原始 active/open 普通令牌上限未改变，专用区活跃
状态不包含在普通计数中，不能混淆这两个计数。所有对照若采用此修复，
必须使用相同开关并标为修复版。取消开关可退回旧执行分支；需要回退
源码时使用上述副本或逆向补丁并重新编译。不改变磁盘映射格式。

已完成实际客机编译，配置为 `DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0
ROCKSDB_PLUGINS=zenfs -j4 db_bench`，退出码 0。原 11 项分配控制流测试
通过；新增
[`tests/run_migration_reserve_test.sh`](tests/run_migration_reserve_test.sh)
提取生产函数，16 项边界测试通过，覆盖默认关闭、单空区留给迁移、
两个分配线程争抢、普通目标写满归还令牌、专用区写满换区、空间不足、
无有效数据重置、Finish/Reset/Close/扫描错误与预留回收补充。设备与
Zone 使用内存替身，不模拟 NAND 或映射持久化，不能代替客机实验。
开关关闭/开启的 1000 条填充与随机读，以及开启后的重新挂载校验和
1000 条顺序读，均实际退出 0；小测试不能证明执行过 ZC。
另用 AddressSanitizer 与 UndefinedBehaviorSanitizer 运行上述边界测试。
默认泄漏扫描在宿主调试环境下报 `LeakSanitizer does not work under ptrace`，
退出 1，原始错误单独保存；仅关闭不受支持的泄漏扫描后，16 项测试
实际退出 0，未报告地址或未定义行为错误。没有完成泄漏扫描验证。
编译、单元与烟雾日志见
[`results/migration-reserve-20261011/`](results/migration-reserve-20261011/)。

真实负载使用独立子镜像
`/home/data/xwz/rrzc-baselines/migration-reserve-20261011.qcow2`，基于停止的
`allocation-fallback-20261011.qcow2`，不写入原始或旧实验镜像。
烟雾日志完整归档后重启，确认 32 个空 Zone，再执行与失败重复轮相同的
50 万条负载（一次填充、两次覆盖、随机读 1000 次）；仅新增迁移预留
开关，其余保持 8 GiB、每 Zone 256 MiB、QLC、最大活跃 6、
16 MiB SST 目标、前台 10 MiB/s、后台 32 MiB/s、分配回退开启、RR 关闭。
主负载填充与第一轮覆盖各 500000 次完成；第二轮进度输出超过 450000 次
后停顿，没有完成第二轮和末尾随机读。保留现场后人工 Ctrl-C，状态 130。
不是完整通过。主日志累计 6 轮 ZC，迁移 `455265464` 字节，重置 7 个
源 Zone，ZC 迁移错误 0；普通部分区回退成功 32 次、分配返回空 2 次。
`06:56:52.127034`、`06:58:46.580861` 两次实际使用专用区
`Zone=805306368`，分别申请 `16196608`、`16212992` 字节，确认预留目标
真实可用，但本轮没有验证所有换区、重新挂载接管等边界。

| 本轮末段现场（客机 UTC） | 已确认事件 |
| --- | --- |
| `06:59:07.021348` | 空闲比例 6%，无效比例门槛 58%，27 个满 Zone，0 空 Zone，2 个部分 Zone，选中 0 个 victim |
| `06:59:07.537525` | flush 分配返回空；随后 RocksDB 设置后台错误和停止标志 |
| `06:59:12.460589` | compaction 分配返回空；随后任务 135 报错 |
| `06:59:13` | 任务 135 删除本次产生的输出 SST；这不是成功安装 compaction 结果后的输入删除 |
| `06:59:17.021498` | 空闲恢复至 20%；后续快照显示 4 个空 Zone，但写入没有恢复 |

报错前该次满区候选中，排除原规则不接纳的“100%”后，最大无效比例
为 51%，低于 58% 门槛。100% 候选实际仍有 7902 字节有效数据，被原
victim 谓词排除。因此本次确认为无空 Zone、没有满足原规则的 victim，
**不是 ZC 找不到迁移目标**。分配返回空后的后台错误、写入停止与输出
清理有实际日志依据；源码和事件顺序表明，稍后的空间恢复来自失败任务
的清理，不能将它当作“等待更久就一定可成功”的证据。本轮没有擅自
改变 victim 门槛、取消 compaction 空间保护或延长分配等待。

中断后报告空闲 1504 MiB、已用 4453 MiB、可回收 1465 MiB。
独立执行 `verifychecksum,readseq`，实际校验通过、顺序读取 500000 个键，
状态 0。仅验证此次存量 SST，不验证第二轮完成、最新值或关闭 WAL 时
未完成写入的持久化。主负载和后校验原始日志分开保存，主日志与客机
SHA-256 比对一致；源码、二进制身份和初始/现场/结束设备报告均归档。
原始负载与退出状态见
[`results/migration-reserve-20261011/rrzc-single-migration-reserve-500k-20261011/`](results/migration-reserve-20261011/rrzc-single-migration-reserve-500k-20261011/)。
归档和日志校验完成后已关闭本轮 8089 实验 VM；源码和二进制保留在本轮
子镜像中，易失性 ZNS 内容不保留。原有 8080 VM 仍运行，原始镜像未改。

结论：编译、27 项控制流/资源测试和小读写通过，真实 ZC 已能使用专用
预留区，本轮未出现迁移目标失败；但 50 万条负载仍未完整通过，不能
认定为稳定基线，也不能比较本轮与旧轮迁移字节来声称性能收益。
建议下一步先减少预试条目数，给 compaction 的临时输出留出空间，保持
20% 和 victim 规则，找到完整且重复运行成功的配置；再单独处理下述
RR 兼容问题。尚未执行更小负载，不把建议当成实验结果。

当时的明确边界（后续兼容修改见下文）：专用部分区持续持有 busy，因此该版 RR 原型的源区
`Acquire()` 会跳过它；本轮只验证 ZC 资源修复。尚未完成专用区作为
RR 源的兼容处理，**不能直接据此开始 RR 开/关收益对照**。重新挂载
超额活跃区接管与容量耗尽等情况也需要进一步真实负载验证。现有
`FinishCheapestIOZone()` 无进展时的循环问题未在本轮顺手改动。

## 2026-10-11：45 万条缩负载验证

按用户“继续”的要求，本轮只将每个写入阶段条目数从 500000 降至
450000；不修改源码、迁移预留、分配回退、GC 20% 或 victim 规则，
不扩大设备，不重新编译。沿用上一轮编译并测试过的修复版二进制，
不能称为原始 ZenFS 基线。其余参数继续采用 8 GiB、32 个 256 MiB
Zone、QLC、最大活跃 6、16 MiB SST 目标、前台 10 MiB/s、后台
32 MiB/s、随机种子 12345，主机端 RR 关闭。设备读数阈值继续为 16，
仅沿用功能测试配置，不表示已标定真实 QLC 的读干扰阈值。

第一轮新子镜像为
`/home/data/xwz/rrzc-baselines/migration-450k-a-20261011.qcow2`，
以停止的 `migration-reserve-20261011.qcow2` 为父镜像，旧镜像不写入。
客机确认 32 个空 Zone，二进制、三个相关源文件及负载脚本的 SHA-256
与上一轮一致。设备为未挂载的 `/dev/nvme0n1`，系统根盘为 `sda`。
仅初始化本轮空白实验 ZNS；未运行会清除旧文件的 `init.sh`。

实际负载命令（隔离客机 root，已格式化实验设备）：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh 450000 migration-450k-a-20261011 \
  16777216 10485760 33554432
```

本轮先验证完整运行、实际 ZC 和独立存量校验；第一轮通过后才以相同
父镜像建立新的子镜像重复。两轮将分别保留初始设备报告、身份、主负载
退出码、主 ZenFS/RocksDB 日志与后校验日志，不合并不同阶段的计数。
第一轮已实际通过：三个写入阶段各完成 450000 次，随机读 1000 次全部
找到，主负载状态 0；独立 `verifychecksum,readseq` 校验通过、顺序读取
450000 个键，状态 0。主日志 2 轮 ZC，迁移 `358316997` 字节、重置
4 个源 Zone，分配返回空和 ZC 迁移错误均 0，部分区回退成功 6 次。
迁移预留已取得，但本轮没有实际使用专用区作为目标，不能将本轮成功
归因于专用目标迁移收益。结束报告空闲 2438 MiB、已用 3876 MiB、
可回收 1076 MiB；报告仍包含预留区剩余空间。
结果及实际退出状态保存在
[`results/migration-450k-20261011/run-a/`](results/migration-450k-20261011/run-a/)。

主负载和后校验日志完成归档、SHA-256 与客机比对一致后正常关机；
等待第一轮进程退出才启动第二轮，没有强制退出或并发执行两轮。
第二轮子镜像
`/home/data/xwz/rrzc-baselines/migration-450k-b-20261011.qcow2`
同样基于 `migration-reserve-20261011.qcow2`，不是基于第一轮子镜像，
避免继承第一轮客机改动。第二轮确认 32 个空 Zone，各项身份一致，
命令参数仅将日志标签改为 `migration-450k-b-20261011`。
第二轮也已实际通过：填充、两轮覆盖各完成 450000 次，随机读 1000 次
全部找到，主负载状态 0；独立校验通过并顺序读取 450000 个键，状态
0。主日志 1 轮 ZC，迁移 `24344005` 字节、重置 1 个源 Zone；普通
分配返回空、ZC 迁移错误、部分区回退成功和专用目标使用次数均为 0。
结束报告空闲 2027 MiB、已用 3895 MiB、可回收 1476 MiB。
原始结果保存在
[`results/migration-450k-20261011/run-b/`](results/migration-450k-20261011/run-b/)。

| 同参数 45 万条预试 | 主负载退出码 | 后校验退出码 | 主负载 ZC 轮次 | ZC 迁移字节 | 源 Zone 重置 | 普通分配错误 | ZC 迁移错误 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 第一轮 A | 0 | 0 | 2 | 358316997 | 4 | 0 | 0 |
| 重复轮 B | 0 | 0 | 1 | 24344005 | 1 | 0 | 0 |

两轮初始状态、设备参数和身份均核验；主与后校验日志分别归档，日志
SHA-256 与客机一致。主 RocksDB 日志均未检出 `Zone allocation failure`、
`Compaction too large`、`Corruption` 或 `[ERROR]`。现存 SST 的校验通过
不验证每个键的最新值，也不验证关闭 WAL 时最后 memtable 的持久化。
两轮均包含实际 ZC，但迁移量和重置数明显不同，**只确认本次配置完成
两次完整运行，不能认定维护开销已统计稳定**。没有将写入限速下的吞吐、
小样本次数或与 50 万条失败轮的不同负载结果当作性能改进证据。
两轮均没有实际使用专用目标，也未完成重新挂载超额活跃区接管等边界
的集成验证；这些不能用本次负载通过来替代。
完整归档后两轮实验 VM 均已正常关机，两个新子镜像保留，易失性 ZNS
内容不保留；原有 8080 VM 仍运行，原始和父镜像未写入。

本轮只更新本文件及保存实验结果，没有修改代码或重新编译，原有默认
行为、GC 20% 和 victim 规则不变。下一步可以用 45 万条作为候选预试
负载，先处理专用预留部分区与 RR 源区选择的兼容，再做相同配置下的
ZC-only / ZC＋独立 RR 对照。正式性能与维护收益比较仍需更多重复，
检查 compaction 时序和负载状态差异；尚未开始 RR 对照或最终 RR-aware
选区公式设计。

## 2026-10-11：RR 源区与迁移预留兼容

承接 45 万条缩负载验证，本轮只修复“专用部分区无法成为 RR 源”的
资源兼容问题，不设计 RR-aware 选区公式，也不改变 GC 20%、斜率 3、
victim 规则、10 秒维护周期或读数阈值。FEMU 本轮未修改。
新增独立补丁
[`zenfs-rr-reserve-compat.patch`](zenfs-rr-reserve-compat.patch)，在
`zenfs-migration-reserve.patch` 后应用；修改三个生产文件：

- `zbd_zenfs.h`：声明 `PrepareReadReclaimSource()`。
- `zbd_zenfs.cc`：先取得 `migrate_zone_mtx_`，再取得
  `migration_reserve_mtx_`，与迁移目标管理锁顺序一致。正在迁移时
  返回“未准备”，留待下一轮；普通源仍需取得 busy，专用源则借用池的
  独占权。重新核验有效字节和写指针，快照失效时不封闭；专用区保持
  池所有权，普通区释放本次取得的独占权。成功 Finish 后，普通部分源
  归还原有活跃令牌，已满普通源不归还，专用源也不归还普通令牌。
  专用源从池中移除、释放独占权并尝试补充空预留，再供后续迁移使用；
  没有空区仍可能导致之后的迁移失败，不保证无限容量。
- `fs_zenfs.cc`：只在 `ZENFS_MIGRATE_RESERVE=1` 时调用新接口；不开
  预留开关时保留原 RR 源封闭分支。`ZENFS_RR_ENABLE` 仍默认关闭，
  快照覆盖与仅迁移 `.sst` 的安全条件不变。

回退：取消 `ZENFS_MIGRATE_RESERVE` 可退回旧 RR 源路径，取消
`ZENFS_RR_ENABLE` 则关闭 RR；源码回退可使用独立逆向补丁，或者修改前
副本 `build-femu/rrzc/rr-reserve-before/`，重新编译。客机修改前副本位于
`/home/femu/rrzc-rr-reserve-before/`。磁盘映射格式不变。

### 编译与边界测试

客机按原配置 `DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0
ROCKSDB_PLUGINS=zenfs -j4 db_bench` 实际编译成功，退出 0。
[`tests/run_migration_reserve_test.sh`](tests/run_migration_reserve_test.sh)
提取实际生产函数，原 16 项加 12 项 RR 源区边界共 28 项通过；原 11 项
分配测试也通过，总计 39 项。新增覆盖普通部分/已满源、预留源、迁移
进行中延后、普通源占用、普通/预留快照失效、空预留不处理、Finish
失败、无空区补充以及后续迁移与源重置不重复归还令牌。内存替身不模拟
设备或完整并发调度；普通令牌由断言核验。28 项另经 ASan/UBSan 通过，
沿用已知宿主限制关闭泄漏扫描，未做泄漏验证。

### 真实设备针对性验证

使用新的独立子镜像
`/home/data/xwz/rrzc-baselines/rr-reserve-20261011.qcow2`，基于停止的
`migration-reserve-20261011.qcow2`，不写入原始或旧子镜像。确认
`nvme0n1` 为未挂载的 8 GiB 实验设备、系统根盘为 `sda`、32 个 Zone
均为空后，只初始化该实验 ZNS。最大活跃仍为 6，设备读数阈值仍为
16，仅用于触发功能验证，不是 QLC 阈值标定。

新增
[`tests/rr_reserve_integration_test.cc`](tests/rr_reserve_integration_test.cc)，
链接真实 ZenFS/FEMU 接口，不替换生产实现，也不直接调用私有 RR 方法。
测试先创建 8 MiB 已知字节模式文件 `/rr-reserve-test/payload.sst`。
**它是以 `.sst` 命名的文件系统测试载荷，不是 RocksDB 生成的 SST，
本测试不能算数据库性能实验。** 通过公开的源区封闭接口预先 Finish
普通源，排除其他部分目标，这一步是测试预置，不计为 RR。随后两次
各执行 20 个真实、直接的 4 KiB 读取，通过设备报告确认达到阈值，
等待真实维护线程按原 10 秒周期处理，不伪造读数或直接触发 RR。

| 实际 RR | 源区起始字节 | 目标区起始字节 | 迁移字节 | 源重置 |
| --- | ---: | ---: | ---: | ---: |
| 普通已满源迁入部分预留区 | 1073741824 | 805306368 | 8388608 | 1 |
| 部分预留源迁入新预留区 | 805306368 | 1073741824 | 8388608 | 1 |

两次源区最大读数均由 20 到重置后的 0；第二次日志明确记录
`RR 封闭专用预留源`，Finish 丢弃尾部 `260046848` 字节且不归还普通
令牌。两次共迁移 `16777216` 字节、完成两次 RR，完整 8 MiB 字节
比对通过，测试状态 0。结束资源统计为实际部分活跃区 1、普通活跃
令牌 0、普通打开令牌 0，专用配额与普通计数没有混淆。本测试没有
施加最大活跃配额下的并发压力，不能用它替代后续完整负载。

测试程序初次编译使用 C++17，因当前 RocksDB 头文件的默认比较运算符
要求 C++20 而失败，退出 1，原始错误保存；核验客机 Makefile 默认
C++20 后，使用以下命令实际编译成功，退出 0，不改依赖或生产代码：

```bash
cd /home/femu/rocksdb
g++ -std=c++20 -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude \
  /home/femu/rrzc-tests/rr_reserve_integration_test.cc librocksdb_debug.a \
  -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt \
  -o /home/femu/rrzc-tests/rr_reserve_integration_test
```

在已初始化的隔离客机中执行，日志目录必须新建且已有同名日志时程序
会拒绝覆盖；不运行 `init.sh`：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  ZENFS_RR_ENABLE=1 /home/femu/rrzc-tests/rr_reserve_integration_test \
  /tmp/rrzc-rr-reserve-output
```

真实 RocksDB 回归另做两组各 5000 条填充与随机读 1000 次：RR 关闭，
分别关闭和开启迁移预留，采用 1 MiB memtable、直接 IO、无压缩、WAL
关闭，两个不同数据库路径，不改上述测试文件。两组均退出 0、随机读
全部找到；开启预留组重挂载后 `verifychecksum,readseq` 通过，顺序读
5000 个键、状态 0。这些数据库小测试没有开启 RR，不能将其当作真实
RocksDB RR 开销对照，存量校验也不证明关闭 WAL 的最新写入持久化。

编译、39 项边界测试、设备报告、身份、小数据库日志和独立 RR 原始
日志保存在
[`results/rr-reserve-20261011/`](results/rr-reserve-20261011/)。
日志 SHA-256 与客机比对一致。结论：预留部分源的兼容缺口已有编译、
资源边界和真实设备路径证据；尚未以本轮新二进制执行 45 万条 ZC-only
或 RR 开启对照，也未验证长期并发压力。下一步用同一新二进制、相同
初始状态重建对照，先观察独立 RR 的行为与开销；阈值 16 的功能测试
结果不能直接用作真实 QLC 或正式性能结论，尚未设计 RR-aware victim
selection 的最终公式。
完整归档后已正常关闭本轮 8089 实验 VM，源码及二进制保留在新子镜像，
易失性 ZNS 内容不保留；原有 8080 VM 仍运行，原始和父镜像未写入。

## 2026-10-11：同一新版二进制的 45 万条 ZC 与独立 RR 对照预试

### 本轮修改与初始状态

本轮不修改 FEMU、ZenFS 生产代码、GC 20% 启动水平或 victim 规则，
不设计最终评分公式。只在
[`run_single_process_pilot.sh`](run_single_process_pilot.sh) 增加可选第六
参数 `zc|rr`：省略时仍为 `zc`，清除继承的 `ZENFS_RR_ENABLE`；仅
`rr` 模式显式设置该变量为 1。原脚本无条件关闭 RR，不能靠外部传入
变量开启，因此必须显式区分模式。非法模式在创建日志或运行负载前
退出 2；`bash -n` 通过，验证输出保存于本轮结果目录。回退时去掉该
参数和对应条件分支即可；生产二进制未变，本轮不需要重新编译。

两组顺序运行，不并发。各自从停止的
`/home/data/xwz/rrzc-baselines/rr-reserve-20261011.qcow2` 新建独立子镜像：

- ZC-only：`/home/data/xwz/rrzc-baselines/rr-compare-zc-450k-20261011.qcow2`。
- ZC＋独立 RR：`/home/data/xwz/rrzc-baselines/rr-compare-rr-450k-20261011.qcow2`。

每组均先确认根盘为 `sda`、未挂载实验盘为 8 GiB `nvme0n1`，32 个
Zone 全空，再仅初始化该隔离实验 ZNS。两份初始化前 Zone 报告逐字
一致。设备组织、QLC 模式、最大活跃 6、读数阈值 16 一致；阈值只用于
功能压力测试，不代表真实 QLC 标定。两组二进制、三个源码文件和脚本
身份文件逐字一致，`db_bench` SHA-256 为
`fd3ae81b5a3c3dec17688e3c6845c072476ffeb876262ba5b90c92539b97b602`，
本轮脚本 SHA-256 为
`c5bf52c6a2fa2aeadc31a02240975a519be3c80781d018fba857678b8f0ea3bc`。
实际 QEMU 参数、宿主二进制哈希和客机身份均已归档。

两组共同开启已有的分配诊断、有限回退和专用迁移预留。这是实验原型
对照，不是未修改的 stock ZenFS 基线。共同负载仍为同一实例内
`fillseq,overwrite[X2],readrandom`：三个写入阶段各 450000 次、4 KiB
值、无压缩、关闭 WAL、直接 IO；64 MiB memtable、16 MiB 目标 SST，
前台 10485760 字节/秒、RocksDB 后台 33554432 字节/秒、种子 12345。
不限制 ZenFS 自身迁移速率。执行命令仅最后的模式与日志标签不同：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  450000 compare-zc-450k 16777216 10485760 33554432 zc

ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  450000 compare-rr-450k 16777216 10485760 33554432 rr
```

### 实际结果：完成负载不等于维护无错误

以下仅统计各自主负载的 ZenFS 日志，不包含后校验：

| 核验项 | ZC-only | ZC＋独立 RR |
| --- | ---: | ---: |
| 主负载退出状态 | 0 | 0 |
| 完成写入次数 | 1350000 | 1350000 |
| 随机读找到的键 | 1000/1000 | 1000/1000 |
| ZC 尝试轮次 | 1 | 6 |
| ZC 迁移字节 | 24348161 | 507827945 |
| ZC 源区重置数 | 1 | 7 |
| ZC 迁移失败次数 | 0 | 4 |
| RR 尝试次数 | 0 | 30 |
| RR 完成次数 | 0 | 27 |
| RR 迁移字节 | 0 | 4522818368 |
| RR 检查失败次数 | 0 | 3 |
| 普通 Zone 分配失败次数 | 0 | 2 |
| 分配回退成功日志数 | 0 | 52 |
| 专用预留目标使用日志数 | 0 | 19 |
| 专用预留源 RR 封闭日志数 | 0 | 1 |

RR 迁移字节包含失败尝试中已实际搬运的部分，不能将 30 次尝试误写成
30 次成功，也不能丢弃失败尝试的开销。ZC 6 轮中仅第 1、4 轮实际迁移
并重置源区，其余 4 轮无目标而失败。这里的重置数不是设备所有擦除数。

两组的填充、第一次覆盖、第二次覆盖实测时长分别为：ZC-only
`177.297 / 177.453 / 177.527` 秒，RR 组
`177.370 / 177.369 / 226.545` 秒。随机读分别为 `3.796 / 4.335` 秒。
这些是单次受限速预试的原始输出，不是统计显著的性能结论。RR 组末段
确有空间错误与后台降速，不能把其延长时间全部归因于单纯 RR 迁移，
也不能将两组直接用于最终策略收益比较。

主实验结束时 `zenfs df` 原始输出的空闲/有效/可回收分别为 ZC-only
`2034 / 3876 / 1512 MB`，RR 组 `705 / 4346 / 2371 MB`。
这里沿用工具的数值及 MB 标签，未用这些显示值计算字节级开销。

### 错误证据与研究观察

RR 主日志为 `09:28:23` 文件，时间均为客机 UTC：

- `09:33:20`：记录一次 `RR 封闭专用预留源`，随后完成迁移，说明上一轮
  新增的预留源兼容路径在完整 RocksDB 负载中实际执行；不是仅看开关。
- `09:35:19`：空闲 13%，无效比例门槛 79%；选中 Zone 起点
  `3758096384 / 6979321856 / 7784628224`，无效比例
  `88% / 82% / 90%`，最大块读数 `912 / 422 / 72`，各有 64 个物理块
  达阈值。该轮随后迁移 110753830 字节并重置 3 个源区。这是当前低
  阈值负载中 ZC 与 RR 风险同时存在的观察，不证明替换选区即可避免
  某个确定的未来 RR。
- `09:36:31`：RR 第 23 次完成后，ZC 第 2 轮尝试迁移但没有可用目标。
  专用预留此前已被实际使用多次；单个预留区并不保证后续每个迁移
  片段都能获得足够空间。
- `09:36:41`：RR 第 24 次和 ZC 第 3 轮均报无迁移目标，GC 空闲比例
  为 3%。`09:36:48` 普通分配失败，扫描中无空区、无可用普通部分区；
  RocksDB 记录 `Zone allocation failure`，后台错误为软错误
  `auto_recovery=1, stop=0`，并出现 `Compaction too large`、写入降速。
  随后错误 compaction 的部分输出删除、RR 和 ZC 又有成功迁移，主负载
  最终完成；不能将这些输出删除误当成成功 compaction 回收输入。
- `09:37:52`：RR 第 29 次已迁移 80497321 字节但没有重置源区，随后
  报无目标；`09:38:02` RR 第 30 次与 ZC 第 6 轮再次失败，随后出现第
  二次普通分配失败。主进程退出 0 不能掩盖这些维护失败。

当前可确认：真实数据库中独立 RR 已产生额外迁移，ZC 候选存在块级
读风险，预留目标和预留源路径均运行过。仍未确认：失败时具体片段的
容量请求、普通区匹配限制与预留补充之间的完整因果链，以及在不同
读分布、合理阈值下可由 ZC 避免的未来 RR 成本。现有读风险来自所有
实际设备读取，包括 compaction 与维护读取；本负载没有单独控制物理
块冷热分布。大量候选块在阈值 16 下已饱和，不宜立即拟合最终评分。

### 校验、归档与后续边界

主负载日志与 RocksDB `LOG` 保存后，两组均另启进程，以
`ZENFS_RR_ENABLE=0`、关闭自动 compaction，执行
`verifychecksum,readseq`；各读到 450000 个键，退出 0。该检查验证存量
SST 可读和校验和，不证明关闭 WAL 的最新 memtable 写入全部持久化，
也不校验所有覆盖写的最新值。后校验读取不计入上述主实验维护统计。

完整原始结果位于
[`results/rr-compare-450k-20261011/`](results/rr-compare-450k-20261011/)，
按 `zc/`、`rr/` 分组，包含主负载、退出状态、后校验、设备快照、
RocksDB 原始日志、故障过程快照与身份文件。两份主 ZenFS 日志 SHA-256
与客机保存值一致。全部归档后，两台 8089 实验 VM 先后正常关闭；
原有 8080 VM 保持运行，原始和父镜像未写入，两个新子镜像保留。

下一步优先定位 RR 开启后迁移空间保障不足的问题，先以本轮日志和
最小边界测试核验；不擅自改 GC 20%、扩大预留规模或改变 RR/ZC 顺序。
在维护无错误的共同负载可重复运行后，再校准读负载与阈值、追踪 Zone
重置代次及对应有效数据，分析真正可避免的 RR，最后研究综合选区收益。
本轮尚未实现 RR-aware victim selection，也未得出其性能收益。

## 2026-10-11：预留目标无替补时保留尾部

### 定位结果与最小修改

对照上轮真实日志，`09:36:31` 的专用目标在最后一个片段后剩余
`57613312 - 17697280 = 39916032` 字节；紧接着 ZC 尝试迁移
`000008.sst` 而失败。该文件最终片段长度为 66791094 字节，非稀疏，
按 512 字节对齐为 66791424 字节。另一处 `09:37:52` 失败前，专用
目标尾部为 `80586752 - 16212992 = 64373760` 字节，随后处理的
`000281.sst` 最终片段长度为 65604650 字节。片段与尾部的尺寸不匹配
有日志依据；但最终映射并非故障瞬间原子快照，不能据此重建所有并发
区状态，也不能断言已找到全部空间不足的原因。

代码中确认独立的容量损失边界：`TakeMigrateZone()` 找不到普通部分
目标、且有有效数据的专用预留容量不足时，先 `Finish()` 丢弃尾部，
再补充空区；补充失败便返回空目标。这样不仅本次大请求失败，后续
较小请求也失去原来可用的尾部。该控制流已由新增回归测试复现。

本轮只修改生产文件 `fs/zbd_zenfs.cc` 的 `TakeMigrateZone()`，补丁为
[`zenfs-reserve-tail-preserve.patch`](zenfs-reserve-tail-preserve.patch)，在
`zenfs-rr-reserve-compat.patch` 后应用：

- 仍先按原规则寻找普通部分目标，不修改寿命匹配比较条件。
- 有效预留尾部不足时，在原迁移锁和预留锁内先锁住容量足够的空替补。
  有替补才 Finish 旧预留并交接池所有权；Finish 失败则释放替补，
  保持旧预留所有权。
- 没有替补时返回空目标，保留旧尾部、写指针和独占权，记录实际请求
  字节与剩余容量；后续较小请求仍可使用。大请求仍可能失败，不承诺
  空间回收必定取得进展。
- 无有效数据的预留仍可直接 Reset；默认关闭的
  `ZENFS_MIGRATE_RESERVE`、预留规模、普通令牌额度、RR 源 Finish、
  GC 20%、RR/ZC 执行顺序及 victim 条件均不变。

宿主修改前副本位于 `build-femu/rrzc/reserve-tail-before/`；新客机副本
位于 `/home/femu/rrzc-reserve-tail-before/`。逆向补丁干运行通过，回退可
逆向应用后重新编译；不改磁盘映射格式，不覆盖旧源码或原始镜像。

### 编译与真实验证

[`tests/migration_reserve_test.cc`](tests/migration_reserve_test.cc) 新增
4 项边界，并调整已有 Finish 失败场景以显式提供替补：无空区时保留
约 39.92 MB 尾部并复用较小请求、候选忙/满、跳过不可用候选并成功
交接；同时核验独占权和普通令牌。新断言在旧生产函数下实际失败，
退出 134，原始输出保留；修复后 32 项迁移/RR 边界与原 11 项分配
测试通过，共 43 项。32 项另经 ASan/UBSan 通过，关闭泄漏扫描，未做
泄漏验证。替身测试不模拟 NAND 或完整并发调度。

使用新独立子镜像
`/home/data/xwz/rrzc-baselines/reserve-tail-20261011.qcow2`，基于停止的
`rr-reserve-20261011.qcow2`。按原 `DEBUG_LEVEL=1 LIB_MODE=static
USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench` 实际编译通过，退出 0；
新二进制 SHA-256 为
`eda7626a1f2dfe2b7205846b8f1ae6321b7bc37e87d42265b523aa1d7edb3233`，
新 `zbd_zenfs.cc` 为
`4808ffb8b03c7c1ca7afb181c853a286b20d41fc687e5c6cb58be0467e9db77d`，
其余两个生产文件哈希与上一轮一致。

扩展 [`tests/rr_reserve_integration_test.cc`](tests/rr_reserve_integration_test.cc)，
在实际第一次 RR 搬入部分预留后，暂时取得其他 28 个空 IO 区的独占权，
构造“没有可用替补”边界。**这是受控占用，不是把真实盘写到容量耗尽。**
测试不伪造有效容量、不手动修改生产字段，也不直接调用私有 RR；
载荷仍是 8 MiB 已知模式 `.sst` 文件，不是 RocksDB SST。

真实日志记录：请求 260050944 字节，预留尾部 260046848 字节；大请求
返回空目标，但尾部和写指针不变，随后 4096 字节请求复用同一预留。
占用的空区在边界检查结束后释放。随后原第二次 RR 仍完成；两次各
迁移 8388608 字节、各重置 1 个源区，源最大块读数从 20 清零，完整
8 MiB 字节比对通过，测试退出 0。测试未覆盖容量耗尽下长期并发。

另用真实 RocksDB 做开启预留、关闭 RR 的 5000 键填充与 1000 次
随机读，全部找到、退出 0；重挂载后关闭自动 compaction、继续关闭
RR，`verifychecksum,readseq` 通过并读到 5000 个键，退出 0。
1 MiB memtable 与 1 MiB 目标 SST、4 KiB 值、无压缩、关闭 WAL、
直接 IO；这是小回归，不验证完整 RR-on 数据库压力或最新值持久化。

测试、生产编译、客机身份、设备报告、数据库及集成原始日志保存在
[`results/reserve-tail-20261011/`](results/reserve-tail-20261011/)。三份
原生 ZenFS 日志哈希与客机保存值逐一一致；数据库主回归的轮换日志
也已归档。全部保存后已正常关闭本轮 8089 VM，原 8080 VM 保留；
新子镜像保留源码和二进制，易失性 ZNS 数据不保留。

本轮只修复“无替补却先丢弃尾部”，没有证明它消除了上轮全部空间
错误。下一步以该新二进制建立独立空白子镜像，重新跑相同 45 万条
负载，检查尾部保留日志和失败次数；必要时再依据实际请求尺寸分析
片段可容纳性与维护容量保障，不立即扩大预留或调整 GC、RR 阈值。

## 2026-10-11：尾部修复后的 45 万条完整预试

本轮没有新增生产代码或修改实验脚本，也没有重新编译。使用上节已
通过编译、43 项边界及设备小测试的新二进制，SHA-256 仍为
`eda7626a1f2dfe2b7205846b8f1ae6321b7bc37e87d42265b523aa1d7edb3233`。
从停止的 `reserve-tail-20261011.qcow2` 顺序启动两个独立子镜像，未并发：

- `/home/data/xwz/rrzc-baselines/reserve-tail-zc-450k-20261011.qcow2`。
- `/home/data/xwz/rrzc-baselines/reserve-tail-rr-450k-20261011.qcow2`。

各组均确认实验盘 8 GiB、32 个 Zone 全空后，才初始化隔离 ZNS；两组
初始 Zone 报告与身份文件逐字一致，源码和脚本哈希与上节及上一轮
45 万条脚本一致。设备 QLC 配置、最大活跃 6、阈值 16、原 GC 20%、
victim 条件不变。共同开启分配诊断、有限回退和专用迁移预留，仍是
实验原型而非 stock ZenFS 基线。负载仍为三个阶段各 450000 次写入
与随机读 1000 次，所有其他参数沿用前述对照；实际命令仅标签与模式不同：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  450000 tail-zc-450k 16777216 10485760 33554432 zc

ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  450000 tail-rr-450k 16777216 10485760 33554432 rr
```

### 实际主实验结果

仅统计 `11:51:33` 的 ZC-only 和 `12:04:09` 的 RR 主 ZenFS 日志，
时间均为客机 UTC，不含后校验：

| 核验项 | ZC-only | ZC＋独立 RR |
| --- | ---: | ---: |
| 主负载退出状态 | 0 | 0 |
| 完成写入次数 | 1350000 | 1350000 |
| 随机读找到的键 | 1000/1000 | 1000/1000 |
| ZC 轮次 | 1 | 4 |
| ZC 迁移字节 | 24348161 | 526944293 |
| ZC 源区重置数 | 1 | 7 |
| ZC 迁移失败次数 | 0 | 0 |
| RR 尝试次数 | 0 | 26 |
| RR 完成次数 | 0 | 25 |
| RR 迁移字节 | 0 | 4027630072 |
| RR 空间不足错误 | 0 | 0 |
| RR 并发删除中止 | 0 | 1 |
| 普通 Zone 分配失败 | 0 | 0 |
| 预留尾部保留日志数 | 0 | 0 |
| 预留替换日志数 | 0 | 4 |
| 专用预留目标使用日志数 | 0 | 30 |
| 分配回退成功日志数 | 1 | 23 |

RR 组唯一维护错误发生在 `12:09:44`：第 14 次 RR 尝试记录已迁移
241350701 字节、源区未重置，并返回
`NotFound: 迁移期间文件已被删除`，最后处理的文件是 `000153.sst`。
代码在实际复制后复查 `GetFile(fname)` 与原对象是否一致；不一致时
中止该次迁移并报告错误。该检查仍保留，未把错误改成成功或绕过映射
安全条件。已迁移字节包含这次尝试的实际搬运，不能从开销中剔除；
它可能包含前面已安装映射的其他文件，不能把全部字节都视为最后文件
的浪费。后续 RR 继续运行，因此是 26 次尝试、25 次完成，不是 26 次
成功。主 RocksDB 日志未发现 `No space`、`Compaction error`、
`Background IO error` 或 `Waiting after background` 记录。

本轮没有复现上一版 RR 组的 3 次 RR NoSpace、4 次 ZC NoSpace 和
2 次普通分配失败。但本轮没有触发“无替补时保留尾部”，仅实际执行
有替补的交接路径。因此不能将这次运行无空间错误全部归因于尾部修复，
也不能据单次完成宣布容量保障或重复性问题彻底解决。

ZC-only 三个写阶段时长为 `177.609 / 177.493 / 177.316` 秒，RR 组
为 `177.549 / 177.342 / 177.323` 秒；随机读分别为 `3.811 / 3.553`
秒。前台限速使三个写阶段均接近限速上限，单次结果不能证明 RR 没有
IO 代价，随机读差异也不能作为性能改进证据。阈值 16 未标定；大量
读取仍来自 compaction 和维护，没有受控的块级冷热分布。尚未实现
RR-aware victim selection，更不能把这组结果当作最终机制收益。

`zenfs df` 的空闲/有效/可回收原输出分别为 ZC-only
`2167 / 3875 / 1293 MB`、RR 组 `1575 / 3927 / 1828 MB`，沿用工具
标签，不据此计算设备级写放大或擦除总数。

### 校验、保存与下一步

保存主实验 ZenFS 与 RocksDB 日志后，两组分别另启进程，以
`ZENFS_RR_ENABLE=0`、关闭 RocksDB 自动 compaction，运行
`verifychecksum,readseq`；各读到 450000 个键，退出状态均为 0。
这验证存量 SST 可读与校验和，不证明关闭 WAL 的所有最新覆盖值已
持久化。后校验日志与主维护统计分开，RR 组另保存独立后校验 ZenFS
日志 `12:13:39`。

原始输出、两组初始和结束设备报告、身份、实际 QEMU 参数、主数据库
日志及退出状态保存于
[`results/reserve-tail-450k-20261011/`](results/reserve-tail-450k-20261011/)。
两份主 ZenFS 日志及 RR 后校验 ZenFS 日志的 SHA-256 均与客机值一致。
全部保存后两台 8089 VM 正常关闭，两个新子镜像保留；原始与父镜像
未写入，原 8080 VM 继续运行，易失性 ZNS 内容不保留。

下一步优先以同一二进制和配置复跑独立 RR 组，核验空间错误是否再次
出现；同时区分并发删除中止、容量失败和实际完成的统计口径，不直接
将 NotFound 改成成功。获得可重复的完整负载后，再引入受控读分布、
分析 ZC 能避免的未来 RR；仍不擅自扩大预留、改变 GC 20% 或最终选区公式。

## 2026-10-11：独立 RR 同配置复跑再次出现容量失败

本轮不改源码、脚本、GC 20%、阈值 16、预留规模或选区规则，不重新
编译。新独立子镜像为
`/home/data/xwz/rrzc-baselines/reserve-tail-rr-repeat-450k-20261011.qcow2`，
仍基于停止的 `reserve-tail-20261011.qcow2`。确认实验 ZNS 32 个 Zone
全空后仅初始化该隔离盘；与上轮 RR 组的初始 Zone 报告和身份文件
逐字一致，实际 QEMU 参数亦归档。负载命令只有标签变化：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  450000 tail-rr-repeat-450k 16777216 10485760 33554432 rr
```

### 失败证据

主日志为 `12:19:52`，下述均为客机 UTC：

- 填充和第一次覆盖各完成 450000 次，分别为 177.341、177.350 秒。
  第二次覆盖未完成，最后进度输出为 350000，没有完成汇总；实际最终
  成功写入数尚未确认，主负载没有执行末尾随机读。
- `12:27:54` 第 24 次 RR 源区 `805306368` 有效字节 151561452，
  封闭部分源尾部 116873216 字节。目标预留 `6710886400` 剩余
  69218816 字节，先实际迁移一个片段 65658692 字节，物理对齐请求
  为 65658880 字节，随后尾部仅剩 3559936 字节。
- `12:27:55` 下一片段请求 65609216 字节，没有普通匹配或空替补，
  新保护分支保留 3559936 字节尾部后返回空目标；RR 失败，源未重置。
  随后 ZC 请求 66791424 字节，也因没有目标失败。
- `12:28:05`、`12:28:15` 重复相同不足，尾部仍为 3559936 字节。
  三轮 RR/ZC 共记录 6 次保留尾部，说明修复在完整负载中确实生效，
  但保留的空间不足以容纳实际片段，不能保证整 Zone 迁移完成。
- `12:28:16` 出现两次普通分配失败，分别涉及 compaction 和 flush。
  分配扫描没有取得可用普通空/部分区；扫描早于回退结束日志，不能
  当作同一瞬间的原子状态。RocksDB flush 设置后台错误
  `auto_recovery=1, stop=1`，另记录两个 immutable memtable 等待
  flush 的停止写入，以及 compaction 的 `Zone allocation failure`。
- 失败 compaction 的输出清理、随后 RR/ZC 又释放空间，后续快照
  `space.at-nospace.df.log` 已显示空闲 2241 MB。该文件是在故障发生后
  采集的，**不是报错瞬间的空闲量**，不能用它否定先前容量失败。
  观察至 `12:29:55` 时主进度仍为 350000，后台继续 RR，但没有主写入
  恢复证据。此后人工 Ctrl-C，捕获原退出状态 130，不算实验成功。

### 截至人工中止的维护累计

| 核验项 | 实际值 |
| --- | ---: |
| 主进程退出状态 | 130（人工中止） |
| RR 尝试/完成 | 35 / 32 |
| RR 累计迁移字节 | 5677624111 |
| RR 容量失败/并发删除中止 | 3 / 0 |
| ZC 轮次/失败轮次 | 5 / 3 |
| ZC 累计迁移字节/源重置数 | 594480785 / 10 |
| 普通 Zone 分配失败 | 2 |
| 保留尾部/预留替换日志数 | 6 / 13 |
| 专用预留目标使用/分配回退日志数 | 117 / 19 |

首次容量失败时 RR 累计搬运为 3717976819 字节；其后的累计包含主
写入停止期间继续运行的后台 RR。不能将最终 5.68 GB 与上一轮成功
负载的 4.03 GB 直接比较成开销变化，也不能把 32 次完成当成主负载
完成。第 1、5 轮 ZC 实际搬运并重置，第 2～4 轮失败。

另外核验上轮并发中止：RocksDB 日志 `12:09:42.647887` 明确记录
删除 `000153.sst`，早于 ZenFS `12:09:44.406744` 对同一文件返回
NotFound。原映射安全检查与错误返回保持不变；本轮未复现该中止，
但不能据此认定它不会再发生或将其统计为完成。

### 校验与结论

保存主日志和多份故障快照后，另启进程、关闭 RR 和 RocksDB 自动
compaction，执行 `verifychecksum,readseq`：存量 SST 校验通过，读
到 450000 个键，退出 0。键来自已完成填充及部分覆盖，这不证明第二
轮覆盖完成或最新值已持久化。后校验 ZenFS 日志为 `12:30:45`，
不计入主维护累计。

全部原始数据位于
[`results/reserve-tail-rr-repeat-450k-20261011/`](results/reserve-tail-rr-repeat-450k-20261011/)，
包含主进度、退出状态、故障与恢复观察 RocksDB 日志、初始化和结束
设备报告、身份、后校验及独立 ZenFS 日志。两份 ZenFS 日志 SHA-256
均与客机值一致；全部归档后本轮 8089 VM 正常关闭，原 8080 VM 保持
运行，原始和父镜像未写入，新子镜像保留，易失性 ZNS 数据不保留。

可确认：新尾部保护没有失效，但“持有一个部分预留区”并不等于为
整个 RR 源区预留足够目标容量；本轮即使源还有可丢弃尾部，Finish
后也不能回收有效数据占用，且一次片段要求连续可写空间。45 万条 RR
预试一轮成功、一轮容量失败，当前不具备重复稳定的性能对照条件。
尚未确认所有普通部分区在报错瞬间的并发占用与容量分布，也未验证
任何新容量准入或更大预留方案。

后续建议先保持当前机制，将两组共同负载统一试降至 40 万条，检查
是否仍能实际完成 ZC、独立 RR 且重复无容量错误；这是待验证的负载
调整，不是已经成功的结果。扩大预留或增加 RR 容量准入会改变机制，
须另行说明与选择，不擅自实现，也不重新引入用户否决的 GC 25%。

## 2026-10-11：两组统一降至 40 万条的工作点预试

依据上一轮容量失败，将两组条目数同时由 450000 降至 400000；
本轮不改源码、脚本、二进制、GC 20%、阈值 16、迁移预留规模或
victim 选择规则，不重新编译。仍使用分配回退和专用迁移预留，因此
两组均为实验分配基线，不是未经修改的 stock ZenFS。

两个新子镜像为：

- `/home/data/xwz/rrzc-baselines/reserve-tail-zc-400k-20261011.qcow2`
- `/home/data/xwz/rrzc-baselines/reserve-tail-rr-400k-20261011.qcow2`

均基于停止的 `reserve-tail-20261011.qcow2`，没有写入父镜像。各自
确认隔离 ZNS 的 32 个 Zone 全空后仅初始化该实验设备。两组初始
Zone 报告与身份文件逐字一致，实际 QEMU 参数均已归档；保持 8 GiB、
256 MiB/Zone、QLC 类型 4、RR 阈值 16、最大活动 Zone 数 6。
`db_bench` SHA-256 仍为
`eda7626a1f2dfe2b7205846b8f1ae6321b7bc37e87d42265b523aa1d7edb3233`。
RR 组结束后的二进制、三个源文件和脚本哈希均与初始值相同；ZC-only
结束时保存的二进制和脚本哈希也与初始值相同。身份文件中的
`db_bench` 与 `./db_bench` 是路径表示差异，不能将整文件比较不一致
误认为程序内容改变。

负载命令为：

```bash
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  400000 tail-zc-400k 16777216 10485760 33554432 zc
ZENFS_ALLOC_DIAG=1 ZENFS_ALLOC_FALLBACK=1 ZENFS_MIGRATE_RESERVE=1 \
  bash /tmp/run_single_process_pilot.sh \
  400000 tail-rr-400k 16777216 10485760 33554432 rr
```

每组填充一次、覆盖两次，共 1200000 次写操作，末尾随机读取 1000
次；三个写阶段共用同一进程。4 KiB 值、不压缩、关闭 WAL、64 MiB
memtable、16 MiB SST 目标、前台 10 MiB/s、RocksDB 后台 32 MiB/s、
固定种子和直接 IO 均不变。脚本在 zc 模式清除 RR 开关，rr 模式
开启主机端 RR；ZenFS 自身迁移不受上述后台速率限制。

### 主负载真实结果

主 ZenFS 日志分别为客机 UTC `12:38:57`、`12:50:53`。
以下只统计主进程日志，不包含另启进程的后校验：

| 核验项 | ZC-only | ZC＋独立 RR |
| --- | ---: | ---: |
| 主负载退出状态 | 0 | 0 |
| 完成写操作数 | 1200000 | 1200000 |
| 随机读找到的键 | 1000/1000 | 1000/1000 |
| 实际 ZC 轮次 | 0 | 3 |
| ZC 迁移字节 | 0 | 221704855 |
| ZC 源区重置数 | 0 | 7 |
| ZC 迁移失败次数 | 0 | 0 |
| RR 尝试/完成 | 0 / 0 | 24 / 24 |
| RR 迁移字节 | 0 | 3422465004 |
| RR 容量失败/并发删除中止 | 0 / 0 | 0 / 0 |
| 普通 Zone 分配失败 | 0 | 0 |
| 保留尾部/预留替换日志数 | 0 / 0 | 0 / 1 |
| 专用预留目标使用日志数 | 0 | 15 |
| 分配回退成功日志数 | 0 | 1 |

ZC-only 并非没有 GC 检查：`12:46:17` 空闲比例到整数 20%，进入
选区，但按原策略无效比例门槛为 100%，候选最大为 98%，因此选中
0 个 Zone，没有实际搬运或重置。RR 组则确实发生三轮 ZC，分别
重置 1、3、3 个源区，并完成 24 次独立 RR。

两组主 ZenFS 和 RocksDB 日志没有发现 `NoSpace`、`No space`、
`NotFound`、RR/ZC 迁移失败、普通分配失败或 RocksDB 后台错误记录。
本轮没有执行“无替补时保留尾部”分支，不能据此次无容量错误认定
45 万条时的容量问题已彻底解决，也不能把改善归因于该保护分支。

ZC-only 三个写阶段为 `157.598 / 157.612 / 157.512` 秒，RR 组为
`157.580 / 157.541 / 157.566` 秒；随机读为 `2.721 / 2.513` 秒。
前台限速使写阶段接近限速上限，单次时长不作为性能改进证据。
`zenfs df` 空闲/有效/可回收原输出分别为 `2129 / 3395 / 1892 MB`
和 `1355 / 3395 / 2411 MB`，沿用工具单位与标签。

### 数据校验、保存及研究边界

保存主 RocksDB 日志后，两组分别另启进程，明确关闭 RR 和自动
compaction，执行 `verifychecksum,readseq`。两组校验退出均为 0，
各顺序读到 400000 个键；这验证存量 SST 可读与校验和，不证明关闭
WAL 后所有最新覆盖值均已持久化。后校验 ZenFS 日志为 `12:47:48`
和 `13:09:55`，与主实验日志分开保存、分开统计。

原始数据位于
[`results/reserve-tail-400k-20261011/`](results/reserve-tail-400k-20261011/)，
包括初始化和结束设备报告、身份、QEMU 参数、主及后校验输出、退出
状态、RocksDB 日志和独立 ZenFS 日志。两组共四份 ZenFS 日志哈希
均与客机保存值一致。全部归档后两台本轮 8089 VM 正常关闭，原有
8080 VM 仍运行；原始与父镜像未写入，新子镜像保留。关闭后易失性
ZNS 内容不保留。

结论仅是：40 万条的 RR 开启组本次能完整完成，并实际运行 ZC 与
独立 RR；尚未复跑确认稳定性。ZC-only 组没有实际 ZC，因此这组
结果不能声称两侧都验证了 ZC，更不能作为 RR-aware 选区的收益证据。
下一步优先同配置复跑 RR 开启组，核验完成度、容量错误、并发删除
和数据完整性。确认重复工作点后再安排受控块级冷热读分布、阈值
校准及维护重叠观察；当前还没有实现 RR-aware victim selection。
如果后续需要两侧均发生实际 ZC，应另行选择共同负载，不直接改变
GC 20%、预留规模或选区规则。

## 2026-10-11：40 万条独立 RR 同配置复跑

在补齐上一轮记录后，使用新的独立子镜像
`/home/data/xwz/rrzc-baselines/reserve-tail-rr-repeat-400k-20261011.qcow2`
复跑 RR 开启组。父镜像、程序、源码、脚本、GC 20%、阈值 16、设备
组织、迁移预留和速率配置均不变；与首轮 RR 组的初始身份及 32 个
空 Zone 报告逐字一致。仅日志标签改为 `tail-rr-repeat-400k`。
没有修改源码或重新编译，也没有修改默认行为。

### 真实运行与错误口径

主 ZenFS 日志为客机 UTC `14:58:01`，结果如下：

| 核验项 | 本次 RR 复跑 |
| --- | ---: |
| 主负载退出状态 | 0 |
| 完成写操作数 | 1200000 |
| 随机读找到的键 | 1000/1000 |
| RR 尝试/完成 | 23 / 22 |
| RR 迁移字节 | 3943629454 |
| RR 容量失败/并发删除中止 | 0 / 1 |
| ZC 轮次/迁移失败 | 3 / 0 |
| ZC 迁移字节/源区重置数 | 163665166 / 7 |
| 普通 Zone 分配失败 | 0 |
| 保留尾部/预留替换日志数 | 0 / 2 |
| 专用预留目标使用/分配回退日志数 | 20 / 1 |

第 7 次 RR 尝试在 `15:01:54.581032` 因并发删除返回
`NotFound: 迁移期间文件已被删除`，该次已迁移 187413990 字节、
源区未重置，最后处理的文件为 `000080.sst`。RocksDB 日志明确记录
该文件于 `15:01:54.126928` 被删除，早于 RR 复查。错误原样保留，
不改成成功；已搬运字节仍计入维护开销，不能全部视为最后文件的
浪费。后续主写入与 RR 继续推进，三个写阶段均完整结束。

主 ZenFS 与 RocksDB 日志没有发现容量失败、ZC 迁移失败、普通
分配失败或 RocksDB 后台错误。没有触发无替补时的尾部保留分支。
三个写阶段为 `157.615 / 157.610 / 157.630` 秒，随机读为
`4.904` 秒；`zenfs df` 空闲/有效/可回收为 `2003 / 3396 / 2000 MB`。
本轮与首轮 RR 次数、搬运字节、随机读时长有差异，但仅两次运行、
且存在并发删除，不能从这些差异推导性能或维护策略收益。

### 保存、校验与下一步

主日志保存后另启进程，关闭 RR 和自动 compaction，执行
`verifychecksum,readseq`：退出 0，顺序读到 400000 个键。
校验和与顺序读分别为 `21.753 / 25.896` 秒，后校验 ZenFS 日志
为 `15:06:40`，不计入主维护累计。关闭 WAL 的最新覆盖值持久性
未由此校验确认。

原始数据位于
[`results/reserve-tail-rr-repeat-400k-20261011/`](results/reserve-tail-rr-repeat-400k-20261011/)，
包含并发中止附近的 RocksDB 快照、主及后校验输出、退出状态、设备
报告、身份、实际 QEMU 参数及两份独立 ZenFS 日志。两个日志哈希
均与客机记录一致，实验结束后的二进制、三个源文件和脚本哈希与
初始值逐字一致。全部保存后本轮 VM 正常关闭，子镜像保留，原有
8080 VM 未停止，父镜像与原始镜像未写入。易失性 ZNS 内容不保留。

40 万条 RR 开启组现有两次完整运行，均实际完成 ZC/RR、未记录
容量失败；第二次有一次并发删除中止，因此不能声称所有维护操作
均成功，也不能宣布普遍稳定。ZC-only 仍只有一轮，且没有实际 ZC。
下一阶段可以在此候选工作点上检查候选 Zone 内风险分布和后续 RR
事件，再准备受控冷热读观察；应区分并发中止、容量失败和真正完成，
保留原始计数。阈值 16 仍仅用于功能验证，没有完成 QLC 标定；
尚未实现最终 RR-aware victim selection 或证明可避免的 RR 成本。

## 2026-10-11：候选区读风险与后续 RR 的离线观察

本轮只分析已有的三份 40 万条主 ZenFS 日志：ZC-only、首次 RR、
RR 同配置复跑；不混入后校验，不启动新实验，不修改 FEMU/ZenFS、
GC 20%、阈值 16 或 victim 规则。新增只读分析脚本
[`analyze_maintenance_overlap.py`](analyze_maintenance_overlap.py) 及
[`tests/test_maintenance_overlap.py`](tests/test_maintenance_overlap.py)。
测试中的合成日志仅用于解析器验证，不作为实验结果。

### 先核对当前策略与记录的含义

已检查与客机哈希相同的源码副本
`build-femu/rrzc/zenfs-source/fs/fs_zenfs.cc`：

- `GCWorker()` 每轮先执行一次独立 RR，再检查是否进入 GC 选区。
- 名为“ZC 候选”的日志实际输出所有快照中的满 Zone，包括不满足
  空间门槛的区。原实现不是只选无效率最高的一个区，而是将满足
  当前无效比例门槛、且近似无效率小于 100% 的满区加入迁移集合。
- `ReadReclaimOneZone()` 还要求 SST 和快照覆盖条件；块已达阈值
  不代表主机一定能够迁移该区。仅凭风险数字不能判断 RR 资格。
- `Zone::Reset()` 和 `ResetUnusedIOZones()` 没有逐次输出完整的
  Zone 重置代际；`MigrateExtents()` 也只累计源重置数，未逐个标识
  ZC 源的重置代际。设备端 `zns_reset_block_state()` 会清零块读数。

因此，日志中的满区观测、原空间门槛选中、实际完成重置、RR 资格
是四种不同概念。不能把它们混作同一个候选集合或成功事件。

### 统计结果与两条具体线索

每 Zone 64 个物理块依据实际配置和 C1h 汇总代码确认，再作为分析
脚本的显式输入。下表的满区与选中计数按**观测行**统计，同一区
可在不同快照中重复出现，不是不同物理 Zone 的数量；RR 按事件统计：

| 核验项 | ZC-only | 首次 RR | RR 复跑 |
| --- | ---: | ---: | ---: |
| 进入选区的快照数 | 1 | 4 | 5 |
| 满区观测行数 | 22 | 89 | 109 |
| 64 个块全部达阈值的观测行数 | 18 | 73 | 90 |
| 原空间门槛选中观测数 | 0 | 8 | 7 |
| RR 尝试/完成 | 0 / 0 | 24 / 24 | 23 / 22 |
| RR 此前有同地址满区观测 | 0 | 4 | 2 |
| 因明确重置或空区复用而排除 | 0 | 3 | 1 |
| 剩余未见复用证据的关联 | 0 | 1 | 1 |

剩余两条的原始依据如下；时间均为客机 UTC，间隔从候选记录到
后续 RR 开始计算，不是 RR 耗时：

| 日志与源区字节地址 | 满区观测时刻 | 近似无效率 / 有效字节 | 最大读数 / 达阈值块 | 后续 RR / 间隔 |
| --- | --- | --- | --- | --- |
| 首次 RR，805306368 | 12:56:59.544451 | 1% / 268433195 | 112 / 64 | 第 18 次，10.192691 秒 |
| RR 复跑，1610612736 | 15:05:10.207771 | 1% / 268433575 | 430 / 64 | 第 21 次，12.088012 秒 |

两区均未被原策略选中；各后续 RR 完成并重置，实际搬运字节分别
为 268433195、268433575。原日志的观测/开始/完成行号分别为
`3824 / 3943 / 4058` 与 `5024 / 5180 / 5543`，已写入分析 CSV。
首例同轮原空间门槛为 94%，实际选择的是有效字节 5454613 的
98% 无效区；第二例门槛为 82%，实际选择两个 88% 无效区。

这说明“GC 选区时有高读压力满区未选，随后成为 RR 源”的现象
存在，但**不能证明当时改选它更划算**：两个源有效数据均接近整区，
空间回收收益很小、迁移成本很高，并且不在原空间门槛选中集合中。
1% 是原代码的整数近似，不代表精确有 1% 可回收字节。这里的风险
也已经达到阈值，不是“尚未达阈值但预计未来发生 RR”的验证。

此外，首次日志中地址 805306368 在第 18 次 RR 后又发生第 20、
22、23 次 RR；复跑地址 1610612736 在第 21 次后又发生第 23 次。
中间都有 RR 完成并重置或重新取得空区记录，所以后几次不能归入
最初快照的同代风险，更不能把一个 ZC 机会算成避免多轮 RR。
没有完整代际日志时，即使未发现这些证据，也仍只标记为“未见复用
证据，代际尚未完整确认”，不声称同一批数据已经被证明。

另一个资格反例：地址 1342177280 的满区观测风险块为 64/64/64，
但两份 RR 日志分别有 7、8 次“含未覆盖或非 SST 的有效数据”延后。
原日志没有区分这两个原因，不能自行判断具体是哪一个。没有后续
RR 的区也只说明在主日志有限窗口内没有观察到，不代表未来成本为零。

### 可复现方法、验证与下一步

实际结果位于
[`results/maintenance-overlap-400k-20261011/`](results/maintenance-overlap-400k-20261011/)，
含来源哈希、中文汇总、220 条候选关联、47 次 RR 事件和解析器测试
日志。脚本每个候选只关联最近后续 RR；同时反向检查每次 RR 的
最近此前候选，用 RR 完成重置、普通空区分配、空预留接管作为
明确复用边界。未计算或导出“可避免 RR 字节”，也没有拟合收益公式。

复现命令如下；输出目录必须不存在，脚本拒绝覆盖已有分析：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 research/rrzc/analyze_maintenance_overlap.py \
  --blocks-per-zone 64 \
  --output-dir /tmp/rrzc-overlap-new-review \
  research/rrzc/results/reserve-tail-400k-20261011/zc/zenfs_nvme0n1_2026-10-11_12:38:57.log \
  research/rrzc/results/reserve-tail-400k-20261011/rr/zenfs_nvme0n1_2026-10-11_12:50:53.log \
  research/rrzc/results/reserve-tail-rr-repeat-400k-20261011/zenfs_nvme0n1_2026-10-11_14:58:01.log
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s research/rrzc/tests -p test_maintenance_overlap.py -v
```

13 项解析器测试通过，涵盖复用排除、最近事件关联、未知读压力、
并发删除中止不算完成、截断/不配对日志拒绝、空间门槛和几何校验、
输出防覆盖。实际三份日志的观测数、选中数、RR 计数与独立人工
核对一致，源码副本哈希与实验身份匹配，原始日志未修改。另在新
临时目录独立复现，三份派生结果逐字一致。
本轮只有 Python 离线分析新增，不重新编译 C++ 或启动客机。

当前工作点的多数满区观测已全部达阈值；C1h 只提供最大值、总和和
几个风险块计数，不能据此恢复完整的逐块读数分布或块上有效数据。
尚无证据支持一个最终 RR-aware 公式或可避免成本的数值结论。
下一步建议先补齐“每次成功 Reset 与代际、候选区 RR 资格”的诊断
记录，并核对快照与读压力采集之间的并发变化；再构造受控冷热读
场景，观察不同风险分布与有效数据迁移成本的权衡。在本节离线观察
结束时该诊断尚未实施；后续实现见下一节，研究方向与原参数不变。

## 2026-10-12：成功 Reset 代际与 RR 资格诊断实现

本轮跨日进行，镜像与结果目录沿用创建时的 `20261011` 标签。
只修改 ZenFS 诊断，不修改 FEMU、GC 20%、阈值 16、原 victim 条件、
RR 检查顺序、迁移预留规模、源区封闭或目标分配策略；未设计评分公式。

### 最小修改与口径

补丁 [`zenfs-maintenance-diagnostics.patch`](zenfs-maintenance-diagnostics.patch)
修改三个生产文件，在 `zenfs-reserve-tail-preserve.patch` 后应用：

- `fs/zbd_zenfs.h/.cc`：`Zone::Reset()` 记录进程内成功 Reset 代际。
  观察序号在调用前后各推进一次，奇数表示 Reset 尚在进行；失败
  不计为成功代际，并标记代际不确定，因为后端可能已重置介质、
  却在随后查询 Zone 状态时失败。后续成功 Reset 重新建立已知边界。
  `GetResetState()`、`GetZoneResetStates()` 只读取原子诊断状态，不取
  新 Zone 锁，也不改变资源配额。所有 Zone 对象从进程内代际 0 开始，
  **不是设备自出厂起的擦除次数**，不持久化、不覆盖外部 Reset。
- `fs/fs_zenfs.cc`：`GCWorker()`、`ReadReclaimOneZone()` 在原快照与
  C1h 查询前后采集诊断状态，按 ZC 候选、RR 扫描、RR 开始三个阶段
  输出日志。保留全部原选区条件与延后/错误返回，不用诊断稳定标记
  过滤或重新选择受害区。原 ZC/RR 日志格式保持不变。
- `ZENFS_MAINT_DIAG=1` 是默认关闭的独立开关。开启后有额外采集和
  日志开销；关闭后不输出新日志，但 Reset 仍更新诊断原子计数。
  不宣称零性能开销，后续比较须统一二进制和诊断开关。

`重置窗稳定=1` 仅表示采集前、采集后、打印时的本进程 Reset 观察
序号和代际一致、无未结束或不确定 Reset；不是全局原子快照，也
不证明文件映射、有效字节或块读数没有并发变化。`含非SST` 只描述
快照覆盖的片段，不能把未被快照覆盖的文件推断为不存在。
`SST覆盖资格=1` 只表示快照非空且有效字节被 `.sst` 后缀片段完整
覆盖；不包含风险阈值、运行时占用、目标容量等条件，不等于一定
能执行 RR。`迁移准入=未检查` 表示本诊断未证明目标迁移预算。
读压力不可用时，块数 0 是占位值，必须结合 `读压力可用=0` 判断。

本轮没有改 ZoneSnapshot 序列化、ZenFS 磁盘格式或 C1h 版本。
旧离线解析器增加对成功 Reset 日志的额外关联边界，不把失败计为
成功；未自动利用全部观测字段，也不将地址关联升级为可避免成本。

### 编译、失败记录与真实短测

使用新独立子镜像
`/home/data/xwz/rrzc-baselines/maintenance-diagnostics-20261011.qcow2`，
父镜像仍为停止的 `reserve-tail-20261011.qcow2`。应用前备份客机
`/home/femu/rrzc-maintenance-before/` 和宿主
`build-femu/rrzc/maintenance-before/` 三个生产文件；原始和父镜像未写入。

编译仍使用 `DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j4 db_bench`，
实际成功。重启后为保存可追溯的日志，用 `make -W` 重新编译两个
修改单元，命令中的额外参数为
`-W plugin/zenfs/fs/zbd_zenfs.cc -W plugin/zenfs/fs/fs_zenfs.cc`，
再次退出 0；持久目录 `/home/femu/rrzc-maintenance-build/` 的编译日志
已归档。最终 `db_bench` SHA-256 为
`8b3770e9a092b496d44b36e504976d81263ba511914c7852eea031fe3352b01b`。

集成测试首次编译访问了私有 `ZenFS::GetFile()`，失败原文保存在
`integration-build.first-error.log`；修正仅使用已有公开 Zone 快照，
没有把生产接口改为 public。另有既有 `CheckRelease()` 的 inline
声明警告，最终测试仍链接成功。第一次设备运行使用 4 KiB 载荷，
20 次直接读取后 C1h 最大读数为 0，退出 1；失败输出、设备结束状态
及原始维护日志保存于 `first-failed-test/`，没有当作成功。

根据 `zns_read()` 只计已建立 NAND 映射的读取、`zns_write()` 在
写缓存满时刷出并建立映射的代码，推断小载荷尚留在 4 MiB 写缓存。
只将测试载荷增至 8 MiB 后，真实 20 次读取得到最大读数 20、一个
块达阈值，支持此解释；没有修改设备计数、阈值或绕过风险条件。
重启只影响本轮独立 VM 的易失 ZNS，失败日志先归档；客机 `/tmp`
清空后，测试程序与构建结果改存持久目录，未依赖旧临时文件。

扩展 [`tests/rr_reserve_integration_test.cc`](tests/rr_reserve_integration_test.cc)，
只在开启新诊断时增加前置资格用例。所有 `.sst` 测试载荷均是已知
字节模式的文件系统测试文件，不是 RocksDB 生成的 SST。成功运行
的原始维护日志为 `rrzc-maintenance-second-test/zenfs.integration.log`，
下述时间为客机 UTC（北京时间为 10 月 12 日）：

| 真实阶段 | 观察到的资格/代际 | 结果 |
| --- | --- | --- |
| 16:10:07 非 SST 8 MiB | 含非SST=1，覆盖完整=1，SST覆盖资格=0 | RR 延后，未迁移或重置 |
| 16:10:17 写入中 SST 8 MiB | 含非SST=0，覆盖字节=0，覆盖完整=0 | RR 延后，未迁移或重置 |
| 删除这两个临时载荷 | 源 1073741824 的代际 0/1、1/2 | 两次成功清空重置 |
| 16:10:27 普通源 RR | 源 1073741824 的代际 2/3 | 搬运 8388608 字节，源读数 20→0 |
| 16:10:37 专用预留源 RR | 源 805306368 的代际 0/1 | 搬运 8388608 字节，源读数 20→0 |

两次 RR 累计搬运 16777216 字节，完整 8 MiB 字节比对通过，测试
退出 0。原预留尾部无替补测试也通过：占用其他 28 个空区，超尾部
请求没有目标，小请求复用保留的 260046848 字节尾部。仅删除了本
测试创建的两份 8 MiB 资格载荷，内容模式可由测试重新生成，全部
日志保留；没有删除用户数据。实际日志的重置窗标记均稳定；窗口
变化、进行中和失败标记只由内存单元测试验证，未宣称真实故障注入。

随后明确关闭 RR、取消 `ZENFS_MAINT_DIAG`，执行真实 RocksDB
5000 键填充及随机读，退出 0、找到 1000/1000 个键；另进程
`verifychecksum,readseq` 退出 0，顺序读到 5000 个键。两份 ZenFS
日志 `16:12:25`、`16:12:35` 均无新维护诊断或 RR 开始记录。这只是
数据库功能烟雾与存量校验，不是性能或关闭 WAL 的最新值持久性证明。

### 保存、回退与下一步

新增 [`tests/run_maintenance_diagnostics_test.sh`](tests/run_maintenance_diagnostics_test.sh)
从生产文件提取真实函数，18 项内存诊断测试通过；原 32 项迁移/RR
资源测试、11 项分配测试通过。离线解析器现在 16 项测试通过，真实
短测日志也可解析出两次 RR 完成、两次资格延后，不把它当作 ZC
重叠证据。该小载荷没有触发实际 ZC。

所有日志、编译退出状态、原始错误、身份、设备报告、QEMU 参数与
解析输出位于
[`results/maintenance-diagnostics-20261011/`](results/maintenance-diagnostics-20261011/)。
三份生产源码和测试源码哈希与客机一致，成功测试前后完整身份文件
逐字一致；失败和成功集成日志、两份烟雾日志哈希均匹配客机记录。
全部保存后本轮 VM 正常关闭，子镜像保留、原 8080 VM 不停止；
关闭后易失性 ZNS 数据不保留。

补丁在宿主与客机的反向 `patch -R --dry-run` 均通过，没有实际回退。
取消 `ZENFS_MAINT_DIAG` 可停止新采集与日志；完全移除计数逻辑则
应用反向补丁并重新编译，备份仅限上述三个文件，不覆盖其他用户
修改。原有 FEMU 工作区的六个修改文件保持原状。

下一步用此已短测通过的版本，在 40 万条 RR 工作点采集一次带诊断
的真实 ZC/RR 日志，核验 ZC 候选阶段、进程内代际及 RR 资格关联。
当前尚未验证真实 ZC 候选路径的新日志，也未验证真实并发重置窗口
变化。完成这一步后再构造受控冷热读场景、讨论未来 RR 成本与选区
权衡，不提前拟合公式或改 GC/RR 策略。

## 2026-10-12：带维护诊断的 40 万条 RR 完整负载

使用独立子镜像 `maintenance-rr-400k-20261012.qcow2`，父镜像为
停止的 `maintenance-diagnostics-20261011.qcow2`。程序仍是上一节已
编译通过的 `8b3770e9…` 版本，本轮没有修改或重新编译 FEMU/ZenFS。
保持 GC 20%、RR 阈值 16、最大活跃区 6、原选区和迁移预留机制，
开启 `ZENFS_MAINT_DIAG=1`，具体参数见
[`results/maintenance-rr-400k-20261012/运行配置.md`](results/maintenance-rr-400k-20261012/运行配置.md)。

先确认 8 GiB、32 个未挂载空 Zone，再仅初始化本实例的易失 ZNS。
单进程填充一次、覆盖两次，每阶段 400000 次写入，共 1200000 次，
主负载退出 0；随机读找到 1000/1000 个键。24 次 RR 全部完成，
实际搬运 3820313097 字节；ZC 两轮分别搬运 5437985、81965662
字节，分别重置 1、3 个源区，总搬运 87403647 字节、重置 4 区。
主维护日志没有 `[ERROR]` 或容量失败记录；这只证明本轮完成。

真实诊断有 103 行：44 条 ZC 满区观测、35 条 RR 扫描、24 条 RR
开始，全部端点重置窗稳定。44 条 ZC 观测中 34 条全部 64 个块
达阈值，42 条满足快照 SST 覆盖资格；观测可能重复同一区，不是
44 个不同 Zone，也不等于全部具备迁移准入。主日志记录 48 次
本进程成功 Reset，包含 RR/ZC 以外重置，不能当作物理擦除总数。
没有真实失败 Reset 或不稳定窗口，本轮不验证这些故障路径。

### 同代际关联及其限制

找到一条未跨重置或空区复用的近期关联，原始时间为客机 UTC：

- Zone `1879048192` 在 `00:30:29.649337` 成为满区观测，代际
  `4/4/4`、观察序号 `8/8/8`；有效数据 268431128 字节，最大读数
  536、64 块达阈值，快照 SST 覆盖完整。近似无效比例 1%，当时
  空闲 14%、原门槛 82%，因此**未被原策略选中**。
- `24.594651` 秒后该区开始第 24 次 RR，代际和序号仍相同，
  有效数据已经变成 134899587 字节；实际搬运此数量并成功重置，
  随后主机代际由 4 到 5。

这比旧日志只有同地址关联更明确，但不是原子数据快照，也不是
已经证明可避免的 RR。提前 ZC 在候选时可能需搬运接近整区数据，
而随后 RR 的有效数据约为其一半；不能把未来成本直接设为当前
有效数据，或声称提前选中就更划算。该区仍在原空间门槛之外，
本轮未证明可在原空间候选集合内获得更优 victim。候选当时已达
RR 阈值，也不是接近阈值风险的预测验证。

Zone `1342177280` 的九次 RR 延后均有覆盖完整、含非 SST、资格
为 0 的真实诊断，不再把这九次延后笼统解释为快照覆盖不足。
具体文件类型尚未据地址确认，不把它直接称为元数据专用区。

### 校验、分析工具与保存

关闭 RR、取消新维护诊断，另进程执行 `verifychecksum,readseq`，
退出 0，校验和检查通过、顺序读到 400000 个键。这不是关闭 WAL
后最新覆盖值的掉电持久性证明。后校验单独保存，不混入主维护统计。
程序、三份生产源码和脚本五项哈希前后逐字一致，两份原始 ZenFS
日志哈希与客机记录匹配。主日志 SHA-256：
`c9cbb343ef84c5e28530ba7188a3db5bd435aa8142addf9111c4420b712422f9`。

仅扩展 [`analyze_maintenance_overlap.py`](analyze_maintenance_overlap.py)
及 [`tests/test_maintenance_overlap.py`](tests/test_maintenance_overlap.py)：
识别新观测、按线程/阶段/地址配对并校验字段；仅在稳定端点代际与
序号一致且无复用或失败 Reset 时标记同一主机代际，仍保留数据
非原子性限制。新增资格/代际 CSV，不把失败 Reset 当作成功边界。
29 项解析测试通过；首次一个合成测试误把写指针设为新区地址，
导致资格校验先失败，修正测试地址后通过，原错误输出仍保留。
三份历史完整负载和上一节短测日志解析回归通过，旧日志不会自动
升级为同代际确认。本轮只改离线工具与说明，不改变系统策略。

全部原始主/后校验、RocksDB 日志、设备报告、身份、解析源码副本
及输出位于 [`results/maintenance-rr-400k-20261012/`](results/maintenance-rr-400k-20261012/)。
先归档并核验，再正常关闭本轮 VM；子镜像保留，原 8080 VM
继续运行，原始和父镜像未写入。本轮关闭后易失 ZNS 内容不保留。

下一步建议构造受控冷热读场景，使空间候选出现不同块风险与迁移
成本，跟踪同代际有效数据变化和实际后续 RR 成本。当前工作点的
风险多数饱和，不能据此拟合最终公式；新二进制/诊断开销与旧版
不同，不做跨版本性能收益结论。

## 2026-10-12：受控冷热读与探索阈值前置验证

本轮新增独立测试，不改 FEMU/ZenFS 生产代码、GC 20%、RR 选区
顺序、预留或默认阈值。完成的是计数口径和风险分布的前置验证，
**尚未完成正式 QLC 阈值校准，也未构造出新的受控 ZC 联合负载**。

### 真实计数口径

代码 `hw/femu/zns/zftl.c::zns_read()` 对每个已映射的 4 KiB 逻辑
页增加所属 block 计数；16 KiB NAND 页由四个逻辑子页表示。
三档真实测试均确认：四次 4 KiB 直接读总共增加 4，一次 16 KiB
直接读增加 4，一次 64 KiB 直接读增加 16。这不是实际 NAND
扰动脉冲次数，不能未经换算/建模就套用文献中的物理读阈值，也
不能认定简单乘四即可还原硬件。没有修改该原始计数口径。

ZenFS `ZoneFile::MigrateData()` 同样通过 `zbd_->Read(..., true)`
发出设备读；业务、compaction 和迁移读到达设备后共用上述路径。
没有按来源分类的设备计数器。本测试没有 RocksDB compaction，
通过独立准备、RR、验证进程分阶段保存日志，避免将校验读混入
受控负载；不声称已对完整数据库负载按来源精确归因。

### 三档真实结果

使用相同程序，在三个独立易失 ZNS 上测试 256、1024、4096。
这些是预先指定的探索值，不是已选定的真实 QLC 阈值。两个区
分别有 32 MiB、128 MiB 已知模式载荷，准备阶段关闭 RR，以
相同读取总量构造单热块和八热块。读取步长根据当前 QLC 映射
选择，最终最大值、总和和达阈值块数由真实 C1h 上报交叉核对。

| 探索阈值 | 每区受控 4 KiB 读次数 | 最终达阈值块 | 实际 RR 完成 | 两区实际迁移量 | 完整字节比对 |
| ---: | ---: | --- | ---: | --- | --- |
| 256 | 2048 | 1 / 8 | 2 | 32 / 128 MiB | 160 MiB 全部通过 |
| 1024 | 8192 | 1 / 8 | 2 | 32 / 128 MiB | 160 MiB 全部通过 |
| 4096 | 32768 | 1 / 8 | 2 | 32 / 128 MiB | 160 MiB 全部通过 |

各档准备、RR、验证均退出 0，源重置后计数归零，主维护日志无
错误。确实能构造非全区饱和的风险分布。热块为 1 或 8 时都只
执行一次整区 RR，不能把风险块数量线性等同于整区 RR 次数。
有效量与热块数同时变化，不能把搬运差异归因于风险块数；每档
读取总量随阈值变化，也不能比较固定负载下的 RR 发生频率。

载荷只是 `.sst` 后缀的文件系统已知模式文件，不是真实 RocksDB
SST。为分离映射，测试显式封闭部分源，分别牺牲 224、128 MiB
尾部；这不是通过覆盖写/删除形成的无效数据。空闲比例保持高于
20%，本轮未触发 ZC，不能据此证明维护重叠或选区收益。

### 新增文件、验证和保留

- [`tests/controlled_read_test.cc`](tests/controlled_read_test.cc)：
  独立真实设备测试程序，已有文件/日志拒绝覆盖；只删除自己刚
  创建的 8 MiB 计数审计文件，已知内容可按代码重建，全部日志保留。
- [`run_controlled_read.sh`](run_controlled_read.sh)：已初始化隔离
  客机的三阶段执行器，不格式化设备；阶段失败即停。`bash -n`
  通过，实际重复执行退出 2、拒绝覆盖，未再次读写载荷。
- [`analyze_controlled_read.py`](analyze_controlled_read.py) 和
  [`tests/test_controlled_read_analysis.py`](tests/test_controlled_read_analysis.py)：
  只读核验阶段退出、身份、日志哈希、C1h 分布及两次真实 RR；
  5 项新增解析测试和原 29 项测试通过，汇总文件也拒绝覆盖。

测试程序链接已有 `librocksdb_debug.a`，两次编译均退出 0，仅第二
版用于正式设备测试；修正的是迁移写锁下暂时缺失快照的轮询处理。
三档均使用测试二进制 `25fbc559…`、源码 `c6e33639…`，原
`db_bench` 仍为 `8b3770e9…`，生产代码不重编译。
配置、完整构建记录、源码及二进制、设备报告、三阶段原始日志和
汇总见 [`results/controlled-read-20261012/`](results/controlled-read-20261012/)。
完整身份前后相同、三档相同，归档日志哈希均匹配；核验后正常
关闭三个实验 VM，子镜像保留，原 8080 VM 继续运行。子镜像
只保留软件和辅助资料，不保存关机后丢失的易失 ZNS 数据。

下一步仍需在固定读写计划下，将风险分布与有效数据量分开控制，
通过真实覆盖写/删除形成无效数据并触发 GC 20% 下的 ZC，比较
多档阈值下的风险演化与后续实际 RR 成本。只有这种联合观察，
才可支持正式实验参数选择；本轮不设计最终选区公式。

## 2026-10-12：四格受控 ZC 与后续 RR，完整重复两轮

本轮只新增独立测试和离线核验，不改 FEMU/ZenFS 生产代码、
GC 20%、原选区条件、RR 处理顺序或默认参数。探索阈值固定
4096，正式 QLC 阈值仍未校准。不同于上一节通过 Finish 封闭
部分区，本轮四个源区都真实写满 256 MiB 后删除测试无效文件。
这是分阶段功能观察，**不是同时启用 ZC/RR 的性能对照**。

### 构造与真实结果

先写满22个填充区，再构造32/128 MiB有效数据 × 单块/八块读取
分布的四格。每组初始固定8192次4 KiB读取；集中组最大8192，
分散组八块各1024，后者初始未达4096阈值。准备使用文件写锁
同步，删除本程序无效载荷后释放，再由原维护线程自然清理。
不会把准备阶段快照未覆盖的区直接算作具备 RR 迁移资格。

第一版短测未共享Zone而退出1。代码和日志确认相同MEDIUM寿命
提示匹配差50，在有空区时优先开新区；只将有效文件提示改为LONG、
无效文件仍为MEDIUM，使用现有差1尾部匹配规则，未改分配器。
重新编译退出0；成功短测真实写满256 MiB、删除224 MiB、剩余
32 MiB完整比对通过，无主动Finish。原失败源码及日志保留。

两个独立子镜像从相同停止的短测父镜像启动，初始32区全空；
并行执行功能重复，未测性能。完整观察点的空闲10%、无效比例
门槛70%，两轮实际 ZC 候选一致：

| 组号 | 有效数据 | 读取分布 | 初始最大读数 | 初始达阈值块 | 原 ZC 选中 |
| ---: | ---: | --- | ---: | ---: | --- |
| 0 | 32 MiB | 单块集中 | 8192 | 1 | 是 |
| 1 | 32 MiB | 八块分散 | 1024 | 0 | 是 |
| 2 | 128 MiB | 单块集中 | 8192 | 1 | 否 |
| 3 | 128 MiB | 八块分散 | 1024 | 0 | 否 |

每轮 ZC 实际一轮搬运67108864字节、重置两个源区。随后开启RR，
第2组真实刷新，搬运134217728字节、源计数归零；第3组仍留原区。
再关闭RR，给第3组追加固定24576次读，累计32768，八块各4096；
重新开启RR，该组也搬运134217728字节并重置。最后关闭RR，对
四个有效文件全部320 MiB字节比对通过。五阶段各退出0，原生日志
无维护错误。填充文件不在本轮做全量字节校验，不能扩大校验范围。

两轮初始Zone报告、源区清单、初始四格CSV、六项软件身份逐字
一致，实际ZC/RR成本也相同。原始候选记录已核对稳定 Reset 窗口
和完整SST后缀覆盖资格；各进程代际从零开始，阶段日志分开解析。
由清单、源位置和跨阶段设备计数核验生命周期，不拼接进程代际。

### 文件与研究边界

新增 [`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)、
[`run_controlled_zc.sh`](run_controlled_zc.sh)、
[`analyze_controlled_zc.py`](analyze_controlled_zc.py) 和
[`tests/test_controlled_zc_analysis.py`](tests/test_controlled_zc_analysis.py)。
测试程序两次编译均成功，生产库未重编译；固定版程序SHA-256为
`66badb65849040bac080c1450175d4f5c319bd2a3a17739d731e0c901063df72`。
新增6项解析测试与原34项共40项通过，执行器语法和实际防覆盖检查
通过；分析汇总也拒绝覆盖。完整配置、失败/成功短测、两轮阶段
输出、原始维护日志、设备状态、身份和核验源码副本位于
[`results/controlled-zc-20261012/`](results/controlled-zc-20261012/)。

先归档核验再正常关机，四个本轮子镜像均保留，原8080 VM继续
运行，原始镜像和停止的父镜像未写入。两轮各删除704 MiB、成功
短测删除224 MiB的本测试无效文件，内容按固定模式可重建；没有
删除用户原始数据，日志保留。关机后易失ZNS内容不保留。

本轮能在相同有效量下区分已有/后续风险，也能在相同读分布下
区分迁移成本，初步说明原空间策略不会因风险不同而改变选中。
但RR先人为关闭再开启，不是生产实际同时调度的问题复现；载荷
不是真实RocksDB SST、没有compaction及有效量随时间变化。
尚未比较另一种选区，也没有“可避免RR成本”或性能改善结论。
当前RR原型在GCWorker中先RR后GC，后续同时维护试验必须核对
该调度的实际影响；不把它说成未修改ZenFS自带的RR行为。

下一步建议在相同初始场景、相同固定读写计划下，保持RR与ZC
同时开启，核对候选是在何次RR/重置之前采集；再加入有效数据
失效过程与多档阈值，评估当前迁移成本和未来实际RR成本。不先
改RR/GC顺序，也不把分阶段结果用作最终选区公式。

## 2026-10-12：同进程开启 RR/ZC 的四格观察，完整重复两轮

沿用上一轮四格、22个填充区、初始每组8192次4 KiB读和最后
24576次固定补读，只扩展测试，不改 FEMU/ZenFS 生产源码、
GC20%、10秒检查间隔、批量空间选区条件或原型 RR先于GC 顺序。
阈值仍为探索值4096。本轮的“同时开启”表示同一观察进程始终
开启两种机制，**不是两种维护并行执行**；GCWorker仍串行先RR后GC。

准备继续使用文件写锁同步。大分散组补读时也短暂持有其文件
写锁，保持维护线程运行，只暂时阻止该文件取得完整快照覆盖资格；
确认八块各4096后释放。不直接调用GC/RR、不主动Finish、不调整
选区来制造结果。这是有同步边界的功能场景，不是无锁异步业务
或公平吞吐/时延对照。全量字节校验另开进程关闭RR，避免与观察
窗口混算；各进程Reset代际不拼接。

### 真实结果与调度影响

先编译独立测试成功，单区短测RR实际迁移33554432字节并重置，
源计数归零，32 MiB字节比对通过。再从停止的短测镜像创建两个
新子镜像执行完整功能重复；初始化前各确认8 GiB、未挂载、32个
完整空Zone。两轮观察及校验均退出0，320 MiB有效数据完整比对
通过，没有维护错误或失败Reset；未全量比对22个填充文件。

两轮的实际维护归属相同：

| 组号 | 有效数据 | 初始分布 | 实际维护 |
| ---: | ---: | --- | --- |
| 0 | 32 MiB | 单块集中，已达阈值 | 第一轮RR，先于本轮ZC候选 |
| 1 | 32 MiB | 八块分散，尚未达阈值 | 第一轮ZC |
| 2 | 128 MiB | 单块集中，已达阈值 | 第二轮维护检查的RR |
| 3 | 128 MiB | 八块分散，补读后八块达阈值 | 补读后的RR |

释放准备锁时空闲10%。RR先迁移第0组、成功Reset后，首次有效
ZC候选空闲变为13%、原无效比例门槛79%；第1组近似无效88%
被选，第2/3组无效50%不选。第2组下一轮被RR处理后，空闲18%、
门槛94%，第3组仍不满足空间门槛。固定补读完成后，第3组RR实际
以八个达阈值块开始。四个原始源区在观察进程均成功从代际0→1；
后续源地址可能被复用为迁移目标，不能要求最终所有旧源仍为空。

第一轮原生日志证据（第二轮核验相同）位于
[`run1/rrzc-concurrent-run1/zenfs.concurrent.log`](results/controlled-concurrent-20261012/run1/rrzc-concurrent-run1/zenfs.concurrent.log)：
第270行准备锁释放边界，第285行第0组RR完成，第333/335/337行
后三组候选、第338行13%/79%汇总，第345行32 MiB ZC统计，
第348/356行第2组RR开始/完成，第409行固定补读边界，
第413/422行第3组八块RR开始/完成。第一轮准备阶段也有一次
20%/100%候选检查，当时无效数据未删除且未实际迁移；不把它
误当成释放准备锁后的四格选区。

| 观察条件 | ZC实际搬运 | RR实际搬运 | 总维护搬运 | 原始源区成功重置 |
| --- | ---: | ---: | ---: | ---: |
| 上一轮分阶段，每轮 | 64 MiB | 256 MiB，2次 | 320 MiB | 4 |
| 本轮同进程开启，每轮 | 32 MiB | 288 MiB，3次 | 320 MiB | 4 |

两轮初始CSV、源区清单与上一轮对应记录逐字相同，四项生产身份
相同；变化只在测试程序和执行条件。这里是功能成本分解对照，
不是相同软件二进制的系统性能对照。静态场景里每个有效文件都
恰好搬一次，成本分类改变而总量不变，**没有证明可避免RR、
减少总迁移或性能改善**。重置数是实际主机Zone Reset，不是
NAND物理块擦除总数。

### 文件、验证与研究边界

扩展 [`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc) 两个
独立模式，原分阶段模式保留；新增
[`run_controlled_concurrent.sh`](run_controlled_concurrent.sh)、
[`tests/run_isolated_concurrent_guest.sh`](tests/run_isolated_concurrent_guest.sh)、
[`analyze_controlled_concurrent.py`](analyze_controlled_concurrent.py) 和
[`tests/test_controlled_concurrent_analysis.py`](tests/test_controlled_concurrent_analysis.py)。
独立初始化器只适用于核实过子镜像身份的隔离客机，先检查目标
未挂载、8 GiB/32区全空再初始化ZenFS；拒绝已有输出目录。
它会设置该客机测试设备mq-deadline，与前轮一致，不能用于原VM。

沿用上一节编译选项，源码/程序改用独立目录
`/home/femu/rrzc-controlled-concurrent-tests/`，旧程序保留。
编译退出0，测试程序SHA-256为
`9415a4a40803eebd7e09ae5c579dffea3d17410eb5dcfdff7fd60360e434fe42`；
源码SHA-256为
`9508c59afeaac54201dfefc71f38381e6a6a9653bce258b936cc137dae7da6d5`。
生产库未重编译；8项新增离线测试与原40项共48项通过。执行器/
初始化器语法、缺参数防护和客机已有目录/结果拒绝重跑均通过，
分析汇总拒绝覆盖。原日志哈希、前后六项软件身份、实际维护
统计、覆盖资格、唯一成功源Reset、补读边界及字节校验均已核验。

日志、源码差异、编译和验证结果集中在
[`results/controlled-concurrent-20261012/`](results/controlled-concurrent-20261012/)，
结构化真实汇总见[`同时维护核验.json`](results/controlled-concurrent-20261012/同时维护核验.json)，
与上一轮初始条件/成本对照见
[`与分阶段对照核验.log`](results/controlled-concurrent-20261012/与分阶段对照核验.log)。
三个新COW镜像均在已批准data目录，父镜像与原始镜像不写入，
旧测试程序未覆盖。先归档核验再正常关机，原8080 VM继续运行；
ZNS易失内容关机后不保留。仅删除本测试的短测224 MiB、两轮
各704 MiB无效模式载荷，可按源码重建，不删除用户原始数据。

本轮仍属于系统分析/收益观察：当前“已到阈值”的组在先RR后GC
原型下可能在ZC候选前已被维护，因此不能直接套用上一轮分阶段
解释。下一步建议加入**未到阈值但接近阈值**的读压力，以及后续
数据失效/固定读取过程，保持当前调度和空间门槛，观察ZC决策前
真正存在的风险、未来RR成本和重复搬运，再进入收益建模。
不先改变调度、不先给最终公式，也不把本轮结果当成RR-aware策略收益。

## 2026-10-12：阈值以下风险与真实失效时序，两个条件各完整一轮

本轮仍不改FEMU/ZenFS生产源码、RR先于GC顺序、GC20%、检查
间隔或选区条件，只新增测试模式和离线核验。沿用8 GiB/32区/
每区256 MiB、64物理块、QLC类型参数4、最大活跃区6，探索阈值
4096，正式QLC阈值仍未校准。原型RR、分配回退和迁移预留开启，
不是未修改的stock基线。

四格仍为32/128 MiB有效量×单块集中/八块分散，真实写满源区
后删除无效载荷。每组初始3584次4 KiB读：集中块3584，是阈值
87.5%；分散八块各448，全部未到阈值。每个大组初始有效文件
拆成64 MiB剩余文件与64 MiB待失效文件，均为`.sst`后缀的已知
模式文件，**不是实际RocksDB SST**。两种条件初始CSV、源区
清单和六项软件身份逐字相同；仅改变真实删除待失效文件的时序。

准备释放文件锁后，两种条件实际原ZC均在10%空闲、70%门槛
选中两个32 MiB组，搬运64 MiB/重置两个源区；两个大组无效
比例50%不选。候选读压力实际为3584/448/3584/448，达阈值
块全为0，集中组80%风险块为1、90%风险块为0。第一次RR
发生在后续补读以后，不再是上一轮已经达阈值而先被RR搬走的场景。

随后给每组固定追加512次4 KiB业务读取。两个小组已迁往同一
目标区，目标区在第0组读后C1总和512/最大512，第1组读后总和
1024/最大576；这些是**共享Zone汇总**，不能当成单文件计数。
两个大组仍在各自原区，集中组累计4096/最大4096/达阈值块1，
分散组累计4096/最大512/达阈值块0。业务读计划相同，不表示
两种条件所有设备读I/O相同：迁移读取本身也计入设备读数。

### 实际成本观察

| 条件（各完整一轮） | 真实失效时序 | ZC搬运 | RR次数/搬运 | 总维护搬运 |
| --- | --- | ---: | ---: | ---: |
| RR前失效 | 补读之前，每个大组删除64 MiB | 64 MiB | 1次/64 MiB | 128 MiB |
| RR后失效 | 集中大组RR完成后，每个大组删除64 MiB | 64 MiB | 1次/128 MiB | 192 MiB |

两种条件均由原维护线程迁移集中大组，四个源区中三个实际成功
Reset到代际1，分散大组代际仍为0。删除后追加12秒有限观察，
分散大组有效64 MiB/无效75%、最大读数512，实际既未选中，也未触发RR；
这不是“永远不会维护”的结论。另进程关闭RR，对剩余四个文件
全部192 MiB逐字节比对通过；填充及已经删除的文件不在该校验范围。
两个完整场景、执行器及校验均退出0，无维护错误或失败Reset。

原始日志关键证据：

- [`RR前失效日志`](results/controlled-near-20261012/before1/rrzc-near-before1/zenfs.near-before.log)：
  第338/340/342/344行四格候选，第345行10%/70%选区汇总，
  第358行64 MiB ZC统计，第368行真实失效边界，第373行补读
  边界，第378/386行64 MiB RR开始/完成，第484行有限窗口的低风险候选。
- [`RR后失效日志`](results/controlled-near-20261012/after1/rrzc-near-after1/zenfs.near-after.log)：
  第358行ZC统计，第366行补读边界，第371/384行128 MiB RR
  开始/完成，第435/438行两个待失效文件的真实删除，第441行失效边界。

两种条件最近此前同地址候选有效量都为128 MiB，且与RR开始
诊断同一主机Reset代际。但RR前失效条件真正RR只搬64 MiB：
**同代际并不保证有效量不变，当前有效量不能直接当作未来RR成本。**
这64 MiB差异来自失效时序，不是RR-aware选区带来的收益；两种
条件采用相同原策略，没有任何新victim机制或避免成本结论。

### 修改与验证

扩展[`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)的
`near-smoke/near-before/near-after/near-verify`，保留旧模式；新增
[`run_controlled_near.sh`](run_controlled_near.sh)、
[`tests/run_isolated_near_guest.sh`](tests/run_isolated_near_guest.sh)、
[`analyze_controlled_near.py`](analyze_controlled_near.py)和
[`tests/test_controlled_near_analysis.py`](tests/test_controlled_near_analysis.py)。
准备和补读的大组仍短暂用文件写锁固定边界，维护线程及RR/ZC
开关不停止；删除的过期文件没有未释放写句柄，实际删除后核验
区有效量。此场景不是无锁业务调度或吞吐/时延实验。

独立客机程序沿用原编译选项，路径`/home/femu/rrzc-near-tests/`，
编译退出0，生产库未重编译，旧程序保留。新程序SHA-256为
`6a7651149b8b8476c7c4c003876ad1bd2541d6e6491d33f6beb9e3133fa8b348`，
源码为`ad7cb991a1cb5a9a222d7f42f72ec258a9cf73788121d90a6b9a08db3136c640`。
先做独立单区短测：真实写满256 MiB、删除128 MiB初始无效文件
和64 MiB过期文件，3584+512次跨阈值，实际RR64 MiB/一次源
Reset，剩余64 MiB全部字节比对通过，再开展上述两个完整场景。
新增7项核验测试与原48项共55项通过，脚本语法、缺参数防护、
已有目录/结果拒绝重跑、分析结果拒绝覆盖均通过。

全部原日志哈希、前后身份、初始和补读记录、实际候选、迁移
统计、成功源Reset分布、失效边界及校验已核验，结果集中在
[`results/controlled-near-20261012/`](results/controlled-near-20261012/)，
结构化汇总见[`近阈值失效核验.json`](results/controlled-near-20261012/近阈值失效核验.json)，
逐字初始条件及成本对照见[`时序对照核验.log`](results/controlled-near-20261012/时序对照核验.log)。
这里只是两个条件各一轮，不冒充重复多轮或统计性能结果。

三个新COW镜像位于已批准data目录；初始化前分别检查独立
测试设备8 GiB、未挂载、32个完整空Zone，再初始化本实例ZenFS
并保持实验设备mq-deadline。父镜像和原镜像不写入，原8080实例
保持运行。先归档核验再正常关闭实验实例，镜像保留软件，易失
ZNS数据不保留。只删除本测试可由源码重建的模式载荷：短测
128+64 MiB、两个完整条件各704+128 MiB；没有删除用户原始数据。

本轮推进到收益建模的输入观察：既需要判断风险多久可能到阈值，
也需要看该时间附近还剩多少有效数据。下一步在**固定同一失效
时序与空间回收约束**下整理候选Zone当前迁移成本、未来实际RR
成本和回收收益，查找真正存在维护重叠收益的选区机会。不要把
改变负载时序的差异当成策略收益，不先给最终公式或改调度。

## 2026-10-12：候选成本表与同等空间预算核验，仅离线整理

本轮不启动VM、不修改设备或生产代码、不改变GC20%、RR/ZC
顺序或现有选区规则，也不设计最终评分公式。重核验上一轮两个
条件的原日志哈希、身份、候选、实际统计和校验后，各自独立
整理四个源区的当前有效/无效量、读压力和基线路径后续同代RR。
输出来源和行号可追溯，不跨失效时序借用另一条件的RR成本。

### 实际数据与实现约束

[`GCWorker()`](../../build-femu/rrzc/zenfs-source/fs/fs_zenfs.cc)当前
会把所有满区中 `garbage_percent_approx > threshold && < 100`
的Zone加入集合，再迁移全部快照片段；**不是排序后只取一个victim**，
也没有本轮达到指定回收量即停止的预算机制。代码SHA-256与客机
实际实验身份一致，摘录及身份已归档。

下表中的无效空间是观察点真实源区数据，RR为实际基线路径
有限窗口记录；容量均256 MiB，初始空闲10%/无效比例门槛70%。

| 组号 | 当前有效量 | 当前无效量 | 最大读数/阈值 | 原门槛选中 | RR前失效条件的后续RR | RR后失效条件的后续RR |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 0 | 32 MiB | 224 MiB | 3584/4096 | 是 | 已被ZC维护，未清理反事实未知 | 同左 |
| 1 | 32 MiB | 224 MiB | 448/4096 | 是 | 已被ZC维护，未清理反事实未知 | 同左 |
| 2 | 128 MiB | 128 MiB | 3584/4096 | 否 | 同代RR实际64 MiB | 同代RR实际128 MiB |
| 3 | 128 MiB | 128 MiB | 448/4096 | 否 | 窗口内未见同代RR | 窗口内未见同代RR |

第2组有80%风险块但未过原空间门槛；第0/1组已经全部被选中。
因此当前场景只在门槛内重排，不会改变初始选中集合。第3组的
未见RR不是永久零成本，第0/1组的已被ZC重置也不能证明未清理
时不会RR。可避免RR及未清理的反事实成本均保留为空/未知。
第2组候选到RR开始间隔实际约11秒，但包含固定补读、写锁同步
和10秒检查间隔，不能把它用作预测到阈值时间。

### 同等空间参考下的静态筛查（未运行替代策略）

以原批次两个小区的**当前无效字节之和448 MiB**作为静态空间
参考。这不是设置新的系统预算，不是证明实际free增量为448 MiB，
也不是原GC达到20%空闲的停止目标。忽略目标容量、预留替换、
Finish、迁移时的失效和读压力变化，只用于排除明显不等价比较。

每个条件独立枚举四个测试源区的15个非空组合，不枚举其他填充
区，也不声称全局最优。保留原门槛且达到参考量的集合只有原
`[0,1]`。假设用集中大区替换一个小区，`[0,2]`虽仍重置两个源区，
源Reset总容量512 MiB相同，但无效空间只有352 MiB，不满足448
MiB参考量，不能据此声称回收同样空间而成本降低。

在**假设允许门槛外源区参与**且满足上述参考量的组合中：

- 追加集中大区 `[0,1,2]`：当前有效量搬运预算192 MiB，比实际
  基线的64 MiB多128 MiB。即使假设其基线RR全部可抵扣且不
  引入新维护，RR前失效条件的静态差额仍为多64 MiB，RR后
  失效条件只能持平。它不是已经运行的另一种选区机制。
- 用一个小区和两个大区 `[0,2,3]`：无效空间480 MiB、当前
  有效量预算288 MiB，多搬224 MiB；借用同条件实测RR作最乐观
  参考抵扣后，静态差额分别多160/96 MiB。`[1,2,3]`账目相同。
- 全部满足空间参考的非基线假设集合，其最小静态差额在两个
  条件分别为多64 MiB和持平。当前四组静态账目没有负差额，
  **不等于证明任何真实策略都无收益**：替代路径的映射、时序、
  后续失效、RR和新增ZC均未运行，参考抵扣不是真实可避免量。

这一步确认当前载荷不足以证明策略收益，不能为了减少RR次数
忽略额外ZC搬运或降低回收要求。后续要先明确候选准入范围与
单轮空间约束，再构造真正有取舍的场景；GC触发仍保持20%，
不擅自将门槛外高风险区纳入实际策略或改变批量清理行为。

### 文件与验证

新增[`analyze_candidate_costs.py`](analyze_candidate_costs.py)及
[`tests/test_candidate_costs.py`](tests/test_candidate_costs.py)，并更新
本记录；已有测试程序、执行器和生产文件不修改。
Python语法编译通过，新增9项测试与原55项共64项通过；真实
两个条件分别产出四行候选记录及15行静态组合预算，逐行成本
合计与原始ZC/RR统计一致。实际防覆盖重跑退出1、已有输出哈希
保持一致，原日志再次重核验通过。没有新的C++编译或设备实验。

结果集中在[`results/candidate-costs-20261012/`](results/candidate-costs-20261012/)，
可读数据见[`候选成本.csv`](results/candidate-costs-20261012/costs/候选成本.csv)，
假设账目见[`未执行组合的静态预算.csv`](results/candidate-costs-20261012/costs/未执行组合的静态预算.csv)，
完整证据及限制见[`候选成本核验.json`](results/candidate-costs-20261012/costs/候选成本核验.json)。
源码、代码证据、测试、输出及防覆盖哈希归档；未删除数据，
原镜像、父镜像、原虚拟机和历史结果未改。

## 2026-10-12：固定未来观察窗口的三档读取计划，各完整一轮

研究范围澄清：原greedy/空间门槛只是对照基线，未来新策略的
候选准入与空间收益权衡本来属于cost-benefit模型，不应单独
当成需要额外确认是否修改greedy的事项。本轮继续第二阶段的
影响因素观察，不修改victim逻辑或设计最终公式。

### 固定条件与测量口径

仍为8 GiB/32区/每区256 MiB、64物理块、QLC类型参数4、最大
活跃区6、探索阈值4096，原RR先于GC、GC20%/10秒维护检查
不变。初始四格与前一轮完全相同：每组3584次4 KiB读，集中
最大3584、分散最大448，真实满区中有效32/32/128/128 MiB。
准备有文件锁同步，原ZC完成两个小区迁移64 MiB/重置两个
源区后，在未来窗口**开始前**各删除大区的一份64 MiB模式
文件。剩余四组32/32/64/64 MiB，失效步骤、初始空间状态及
读取分布不变。三档初始CSV、源清单、六项软件身份逐字相同。

随后释放全部补读写锁，按三个计划速率读取每个有效文件，
业务读持续计划24秒，未来观察到40秒。集中组读固定地址，
分散组以64 KiB步长轮询八块，速率是发送计划而非性能指标。
线程不直接调用RR/ZC、不暂停维护，也不预设未来RR次数。
真实每文件完成192/768/6144次，均达到本轮计划量；若迟滞
导致次数不足，核验器记录实际值，不用目标量冒充完成量。

每约100毫秒读取一次C1，并记录前后主机Reset代际/序号，
稳定时按Zone+代际标记首次采样见到达阈值。它是**首次采样
观测，不是精确的NAND跨阈值时刻**；重置窗稳定也不等于读数
或文件映射的原子快照。另从原生日志解析真实RR开始、完成、
搬运文件及字节，不跨代际关联、不只数原始源地址。按RR开始
是否在关闭请求前计入窗口，关闭时等待已在执行的维护完成；
本轮三档均无关闭请求后新开始的RR。

### 真实结果

| 每文件计划速率 | 每文件真实补读 | 原集中大区首次采样跨阈值 | 该源RR开始（相对窗口） | 窗口RR次数/搬运 |
| ---: | ---: | --- | --- | --- |
| 8 次/秒 | 192 | 未观察到 | 未观察到 | 0次/0 MiB |
| 32 次/秒 | 768 | 16.042秒 | 19.970秒 | 1次/64 MiB |
| 256 次/秒 | 6144 | 2.006秒 | 9.929秒 | 2次/192 MiB |

三档初始ZC实际均64 MiB，未来窗口无额外ZC；初始ZC与窗口RR
合计搬运分别64/128/256 MiB。这是不同未来读取计划的功能成本
观察，**不是新victim策略带来的改善**。低档零RR只指本轮有限
窗口，不能作为永远不触发或统计概率为零的结论。

中档原集中源区7516192768的代际0，RR只搬第2组剩余64 MiB。
高档第一次也是这个源区64 MiB；随后共享迁移目标805306368
代际0在12.731秒首次采样达阈值，20.909秒开始第二次RR。
原生日志显示这次实际搬第0/1/2组文件32+32+64=128 MiB，
源区封闭剩余尾部128 MiB，成功Reset。因此两次合计192 MiB，
不能只统计原集中源区而漏记共享目标维护。分散读取的第1组
也被第二次RR再次搬运，但不据此判断其自身哪些物理块达到
阈值；当前C1仍无完整逐块计数向量。

高档关键原日志位于
[`zenfs.future-high.log`](results/controlled-future-20261012/high/rrzc-future-high/zenfs.future-high.log)：
第375行窗口开始，第376行原源区采样跨阈值，第379/386/387行
第一次RR，第437行共享目标采样跨阈值，第442行第二次RR，
第443/448/453行三个实际迁移文件，第459/460行128 MiB统计
及完成，第560行窗口关闭请求。中档日志第379行首次采样、
第382行RR开始，采样至维护约3.927秒；高档两次相应间隔约
7.922/8.177秒，不能把这些轮询和实际迁移时序间隔当成预测
到阈值的时间或设备延迟性能指标。

这批数据表明模型输入需关注**未来读取计划、有效量变化、
轮询时序以及迁移后的共享布局与再次维护**。重新累积读风险
导致的再次RR不一定是不必要维护；没有替代策略运行，不能
把192 MiB都说成可避免成本。三档同时改变速率和固定窗口内
总读量，尚未独立识别纯速率效应，也不能用各一轮估计概率。

### 实现、验证及归档

只扩展[`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)的
`future-smoke/low/medium/high`，旧模式保留；新增
[`tests/run_isolated_future_guest.sh`](tests/run_isolated_future_guest.sh)、
[`analyze_controlled_future.py`](analyze_controlled_future.py)和
[`tests/test_controlled_future_analysis.py`](tests/test_controlled_future_analysis.py)。
新程序在独立`/home/femu/rrzc-future-tests/`构建，原程序/生产库
未覆盖或重编译。第一次构建退出1，新布尔变量与旧CSV流重名，
只改新变量名再编译退出0；失败源码/日志保留，未在失败构建后
初始化设备。先完成单区64次/秒、8秒读/20秒观察短测：实际512
次读、RR64 MiB/一次源Reset、剩余64 MiB字节比对及哈希通过，
再运行三档完整场景。

成功程序SHA-256为
`b31f95203058467ebaaf9a37e43d03e51d57b388cd7ec8d05228da046cfc9435`，
源码为`1905d74cf71563a9d6ef97e1c8cd4ec7769ca8f6628939b4a1086b5cd5476a1f`。
三档各完整一轮，阶段和执行器均退出0，无维护错误或失败Reset。
随后另进程关闭RR，四个剩余文件全部192 MiB字节比对通过；
不扩大到填充及已删除文件。新7项离线测试与原64项共71项通过，
Python语法、初始化器语法/缺参数/已有目录拒绝重跑、分析结果
拒绝覆盖均通过，所有原日志哈希、身份、采样、实际读量和RR
迁移文件/统计已核验。

完整结果在[`results/controlled-future-20261012/`](results/controlled-future-20261012/)，
汇总见[`未来读取窗口核验.json`](results/controlled-future-20261012/未来读取窗口核验.json)，
可读对照见[`三档真实成本核验.log`](results/controlled-future-20261012/三档真实成本核验.log)。
包含构建失败/成功、短测、三档原生日志、100毫秒采样、真实
业务计数、窗口配置、源码、身份、防覆盖检查和QEMU参数。

四个全新data盘COW镜像均从停止的父镜像派生；分别检查8 GiB、
32个完整空区、未挂载后才初始化独立ZenFS，测试设备mq-deadline
与前轮一致。三个完整场景并行做功能观察，不评价并发性能。
先归档核验再正常关闭，软件镜像保留，易失ZNS内容关机后不
保留。原8080实例和原镜像/父镜像未改；只删除本轮可由源码
重建的模式载荷：短测128+64 MiB、三档各704+128 MiB。

下一步应使用**相同未来总读量、不同发送节奏**，进一步分离
读量与时序；并整理同代源区风险和迁移目标再次维护的成本。
这些属于cost-benefit输入验证，不将原空间门槛绑定新模型，
不先上线最终公式或把不同负载的成本差异说成策略收益。

## 第二阶段补充：相同未来业务读量、不同发送节奏（2026-10-12）

### 对照条件及真实结果

本轮接续前轮三档窗口实验，只分离未来业务读量与发送节奏。
两组均从同一个停止的短测镜像派生，使用相同新测试二进制：
原四格初始每文件3584次读、单块集中最大3584/八块分散最大448，
初始有效量32/32/128/128 MiB、空间状态与候选一致。先按原机制
ZC小组两区，共迁移64 MiB；两组都在未来窗口前真实删除大组
各64 MiB的到期文件，剩余大组各64 MiB。未来窗口不持补读写锁，
RR先于GC、10秒维护轮询、GC20%、阈值4096和已有迁移预留等开关不变。

两组每文件实际未来业务读均为768次4 KiB（四个文件合计3072次），
统一观察40秒，仅发送计划为慢速32次/秒持续24秒、快速256次/秒
持续3秒。这里相同的是**未来业务读次数**，不是所有物理读操作数：
原型读计数还包括维护迁移读取，实际RR会改变文件地址及后续布局。
本轮两组各完整一轮，仅作功能和维护成本观察，不比较性能或估计概率。

| 条件 | 每文件实际未来业务读 | 首次采样见原热源跨阈值 | 实际RR开始（相对窗口） | RR次数/搬运 |
| --- | ---: | ---: | ---: | ---: |
| 慢速，24秒发送 | 768 | 16.050秒 | 19.894750秒 | 1次 / 64 MiB |
| 快速，3秒发送 | 768 | 2.002秒 | 9.913549秒 | 1次 / 64 MiB |

两组RR均来自集中大组的原源Zone `7516192768`、同一主机Reset
代际0，仅迁移第2组剩余64 MiB，源Reset成功，尾部损失0。
未来窗口没有额外ZC、迁移目标再次RR或关闭请求后新开始的RR；
两组初始ZC加未来RR均为128 MiB。分散大组在有限窗口内未发生RR，
不据此认定其永不维护。跨阈值时间是首次采样所见，不是精确发生
时刻；采样至RR开始间隔约3.843437/7.910486秒包含维护轮询时序，
不是设备延迟性能指标，也不是可直接推广的预测常量。

这批真实数据补充了一个边界：**在本轮状态和有限窗口内，更快
发送相同读量只提前RR，没有增加RR次数或搬运量**。不能由前轮
不同读量的结果直接断言纯速率增加维护成本。模型需区分何时可能
发生RR，以及届时有效量和迁移后布局决定的维护成本；两者不能
合并成简单的“读数越大，收益越大”。尚未运行替代victim策略，
本轮仍不证明选区收益或可避免的RR字节。

### 修改、验证及保留结果

只扩展[`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)三个
`future-equal-*`测试模式，旧模式参数不变；新增
[`tests/run_isolated_equal_guest.sh`](tests/run_isolated_equal_guest.sh)，
扩展[`analyze_controlled_future.py`](analyze_controlled_future.py)的
两组配置和`--equal-count`严格核验，以及原单元测试中的四项测试。
核验器拒绝计划768但实际少读、条件缺失或重复、不同软件/初始状态，
拒绝混用实验口径和覆盖结果。没有修改FEMU、ZenFS生产源或原选区行为。

新程序在客机独立`/home/femu/rrzc-equal-tests/`构建，链接已有库，
编译退出0，程序SHA-256为
`f37b29a527477ce15dc15d4041685cf94551f7fa048da8a23db7752ab0c67a0d`，
源码为`f546815976116628ebc9b4c2ea81ae782f13128f3bd5ac17dfc070b06e3c0df2`。
先完成单区3秒发送768次/20秒观察短测：RR一次64 MiB、源Reset、
剩余64 MiB字节比对通过，再运行完整两组。全部阶段及执行器退出0，
无维护错误或失败Reset；两组随后关闭RR另进程逐字节核验四个
剩余文件全部192 MiB通过，不扩大到填充文件或已删除文件。
75项离线测试、Python语法、初始化器语法及缺参数/非法模式拒绝通过；
原三档真实结果经扩展核验器重验一致，仅新增业务读取秒数字段。
两组与短测的测试程序、源和执行器相同，四项生产身份与前轮一致，
全部原日志哈希及实际计数、采样、原生维护统计通过核验。

结果在[`results/controlled-equal-20261012/`](results/controlled-equal-20261012/)，
包括构建源码与差异、短测、两组完整原生日志、CSV计数和采样、
身份/哈希、退出码、QEMU参数、核验器版本和保留镜像信息。
汇总见[`等读量窗口核验.json`](results/controlled-equal-20261012/等读量窗口核验.json)，
简要成本见[`两组真实成本核验.log`](results/controlled-equal-20261012/两组真实成本核验.log)。
所有原始结果保留且无覆盖。三组全新data盘子镜像均保留，初始化前
核实8 GiB、32完整空区且未挂载；两组并行只做功能观察。先归档
核验再正常关机，易失ZNS载荷不保留，软件与构建日志留在镜像。
本轮只删除自己生成的模式载荷：短测128+64 MiB、两组各704+128 MiB。
原8080实例仍运行，原始镜像与停止的父镜像未修改。

下一步进入cost-benefit输入整理：区分当前空间回收/复制成本、
同代源区未来RR时机和届时有效量，以及迁移目标后续维护成本。
候选准入和空间回收约束属于新模型设计，不把原空间门槛绑定新
模型；保留它仅作为基线。尚未确认的是如何预测真实数据库负载的
未来读取与失效，以及替代选区在回收/迁移可行性相当时能否真正
减少总维护成本。继续依据代码和真实对照推进，尚不设计最终公式。

## 第二阶段模型输入整理

整理日期：2026-10-12。本节只汇总已核验代码和历史实验，不新增
实验、不修改机制、不确定最终公式或权重。研究仍是在**ZC已经
触发**后权衡空间回收与未来RR维护，不改成独立刷新调度课题。

### 已确认哪些因素重要

| 因素 | 当前代码或真实实验依据 | 对模型的含义及边界 |
| --- | --- | --- |
| 当前有效/无效量 | 四格有效32/128 MiB，对应无效224/128 MiB；实际初始ZC搬运两个小区共64 MiB | 当前有效量提供复制载荷参考，无效量提供静态回收参考；两者不能只取一个 |
| 区内读风险分布 | 近阈值实验大组同样累计4096读，集中组最大4096、达阈值块1，分散组最大512、达阈值块0；只前者发生RR | 总读数相同不代表风险相同；不能只用Zone总读数。当前RR按整区执行，不能将风险块数直接乘整区迁移量 |
| 未来读取量 | 固定40秒窗口，真实每文件192/768/6144次读，对应RR为0次/0、1次/64、2次/192 MiB | 未来读取计划影响观察到的维护；这三组同时改变读量和节奏，不构成纯速率效应证明 |
| 读取节奏和维护时序 | 相同未来每文件768次读，24秒/3秒发送，RR分别约19.89/9.91秒开始；均一次64 MiB | RR到来时间与维护成本要分开；更快发送不一定增加维护次数或字节。结果含10秒维护轮询，不是设备延迟常量 |
| RR发生前的数据失效 | 初始候选有效量都128 MiB，RR前/后失效条件实际RR分别64/128 MiB | 当前有效量不等于未来RR时有效量；同一Reset代际内有效量也会变化 |
| 迁移目标与再次维护 | 高读量组先迁移原热源64 MiB，再RR共享目标128 MiB，第二次搬运三个文件并封闭128 MiB尾部 | 提前维护源区不代表消灭后续风险；需核算目标维护及尾部损失，不能只跟踪原源地址 |
| 候选集合与回收约束 | 当前代码批量选择全部过空间门槛的满区；静态用大区替换一个小区，回收参考从448降到352 MiB | 仅重排已全选集合可能无效果；候选准入、选中集合及回收约束是新模型的一部分。静态无效字节不是实际可用空间增量 |

证据分别见[近阈值与失效时序](results/controlled-near-20261012/近阈值失效核验.json)、
[未来读量窗口](results/controlled-future-20261012/未来读取窗口核验.json)、
[相同读量不同节奏](results/controlled-equal-20261012/等读量窗口核验.json)和
[候选空间与成本表](results/candidate-costs-20261012/costs/候选成本核验.json)。
以上结论针对当前原型和受控载荷；不合并不同负载计算策略收益，
有限窗口未见RR不代表未来永远零成本，各单轮条件不用于估计概率。

### 输入现在有没有，以及还缺什么

| 输入类别 | 当前已能获取 | 尚未具备或不能直接推出 |
| --- | --- | --- |
| 当前Zone空间状态 | 快照中的容量、有效字节、写指针、满区状态，系统空闲及部分区剩余量 | 无效量只是静态参考；真正可分配空间还受目标占用、预留、Finish和令牌约束 |
| 当前块级风险摘要 | C1h提供阈值、每区最大/总读数、80%/90%/达阈值块数、块数配置 | 主机没有完整逐块计数向量、块身份或逐块有效数据分布；不能重建每块读风险或速率 |
| 历史变化与关联 | 测试CSV时间采样、原生维护日志、进程内Reset代际和观察序号 | 未实现在线趋势预测；汇总值变化不能等同某个固定块的速率。代际不是持久设备擦除计数，采样非原子 |
| 未来读取和有效量 | 受控测试中真实业务读计数、计划发送时间、实际删除边界及RR开始有效量 | 这些是实验计划或事后观测，不是ZC决策时预知的真实数据库未来；尚无未来读取/失效预测器 |
| 迁移可行性 | 快照覆盖、有效文件类型、RR源状态复查、迁移预留及目标分配路径 | 尚无完整候选集合的可行性评估器；源能Reset不保证目标有空间或不引入额外维护 |
| 实际维护成本 | ZC/RR实际迁移字节、尝试/完成、源Reset、RR开始/完成及封闭尾部日志 | 未测完整设备物理读写放大、擦除耗时/总数和可靠的性能对照；RR次数不能单独代表总成本 |

代码依据：
[`ZoneReadPressure`及`ZoneResetState`](../../build-femu/rrzc/zenfs-source/fs/zbd_zenfs.h)，
[`GCWorker()`及`ReadReclaimOneZone()`](../../build-femu/rrzc/zenfs-source/fs/fs_zenfs.cc)，
[`PrepareReadReclaimSource()`及空间管理](../../build-femu/rrzc/zenfs-source/fs/zbd_zenfs.cc)，
[`zns_read()`](../../hw/femu/zns/zftl.c)及
[C1h汇总实现](../../hw/femu/zns/zns.c)。已核对本地ZenFS源与实验身份。
FEMU内部按物理块维护计数，但主机当前只有块级信息的Zone摘要，
**不等于已经获得完整逐块状态**。后续若模型需要每块趋势或精细
分布，应先按需要补接口和验证，不能假设现有C1h已经提供。

### 建议采用的建模框架，尚非机制实现

先分三层组织，不立即确定评分公式：

1. **回收与可行性层**：ZC触发后明确候选准入、单区或集合选择、
   回收目标及复制目标是否可用。保留GC20%作为触发基线；原无效
   比例门槛仅是基线规则，不是新cost-benefit模型不可突破的边界。
2. **未来维护层**：在明确的观察窗口内，分别估计不选该Zone时
   RR是否可能到来、何时到来，以及届时要搬多少有效数据；提前
   ZC后的风险重累积、目标共享与再次维护也要进入同一账目。
3. **综合比较层**：比较当前ZC成本与后续维护成本，同时满足
   回收约束。先以实际迁移字节为可验证成本口径，另记RR/ZC次数、
   Reset和尾部损失；尚未测量的耗时/擦除成本不填猜测值，暂不
   把字节与次数按未经验证的权重相加。

主机RR目前由任一块达阈值触发整区有效数据迁移，且每轮最多
处理一个合格源区；迁移后才可能重新积累风险。因此“多个高风险
块”可以描述风险分布，但**不能直接解释成同代源区必需多次整区
RR**。如何利用风险分布预测窗口内维护，仍是模型需要验证的问题。
当前计数包含业务和维护读取，并未区分来源；阈值4096仅为探索
参数，QLC类型配置不等于真实QLC读干扰/ECC阈值已经校准。

### 下一步该补的证据，而不是先宣布收益

接下来优先构造一个真正有选择余地、回收要求一致的候选场景，
明确两条路径：基线选区集合不含目标高风险Zone；对照集合包含
该Zone并相应调整其他victim。两条路径都完成ZC，保持相同初始
状态、未来业务负载、失效计划、RR机制和观察口径，并检查真实
空间及迁移可行性。不是把基线ZC关闭或只比较相同Reset个数。
这属于后续实验建议，本次未实现或运行替代选区策略。

只有同时统计两条路径的当前ZC、后续源区RR、目标再次维护和
额外ZC，才能判断提前清理是否真正减少总维护。已被基线ZC选中
的Zone，其“不清理时成本”仍未知；基线实测的RR字节也不等于
提前ZC必然可避免的字节。原候选表中的反事实和可避免成本继续
保留未知，不把维护次数下降或不同负载之间的差异直接称为收益。

本次仅修改本文档：新增集中输入整理和导航，并澄清开头计数口径。
重核验已有结果及相关源代码；未新增编译、设备实验、接口或选区
实现，也未修改原始日志、历史结果、镜像和既有用户代码。

同日后续已完成下面的等预算选区对照；此处“不新增实验”仅指
此前输入整理任务，最新实验及其局限以下节记录为准。随后同失效
计划下的配对实验进一步确认：相同首次回收要求下，总搬运差异
既取决于热区未来RR，也取决于另一候选是否在再次维护前失效；
这些受控未来信息尚不是在线可用的预测输入。

## 2026-10-12：首次单区等预算选区与终态空间核验

本轮属于第二阶段的受控因果对照，不是最终公式或策略实现。
原`GCWorker()`会批量选中全部过空间门槛的区，简单重排没有
取舍。因此仅在本轮隔离客机加默认关闭的实验入口，比较**固定
同等首次回收预算下，先选冷区或先选近阈值热区**。两组都启用
此入口，不能把冷区组称为未修改的原批量ZenFS基线。

### 条件与实际结果

8 GiB/32区、每区256 MiB/64物理块、探索阈值4096、GC20%、
10秒维护轮询、RR先于GC及已有分配回退/迁移预留不变。真实
写入24个整区填充文件及两个候选整区，再各删除192 MiB自己
生成的无效载荷；两个候选各有效64 MiB、无效192 MiB，初始
空闲10%、原空间门槛70%，二者都具备空间资格。两区各初始
3584次业务读：热区单块最大3584，冷区八块分散最大448，均
未达RR阈值。冷区地址7516192768先于热区7784628224，作为
空间信息相同情况下的地址顺序参考；另一组显式指定热区。

首次都只ZC一个区、迁移64 MiB、成功Reset一个源区，系统
空闲从768增至960 MiB。首次维护后不持补读锁，两文件都实际
完成768次4 KiB未来业务读，计划32次/秒持续24秒、观察40秒。
窗口后两组追加完全相同的两个整区文件，共512 MiB真实写入，
由原维护线程自行处理剩余候选，测试程序不直接调用ZC/RR。
本轮不安排有效文件后续失效，另进程关闭RR校验剩余128 MiB。

| 首次选择（各完整一轮） | 初始ZC搬运 | 40秒窗口RR | 追加写入后的ZC搬运 | 全部维护搬运 |
| --- | ---: | ---: | ---: | ---: |
| 先清冷区 | 64 MiB | 1次 / 64 MiB | 0 | 128 MiB |
| 先清热区 | 64 MiB | 0次 / 0 | 64 MiB | 128 MiB |

窗口后，冷区优先组空闲1152 MiB、满区可回收0；热区优先组
空闲960 MiB、满区可回收192 MiB。只比较窗口内成本，会遗漏
后一组未完成的冷区回收。相同追加写入后，热区优先组在原GC
线程中再迁移冷区64 MiB；冷区优先组无需额外ZC。终态两组
都空闲640 MiB、有效6784 MiB、满区可回收0、整数空闲8%，
两个原源区在主实验进程中均成功Reset到代际1。IO区占用形态
都为26个满区、2个空区、1个已写128 MiB的关闭部分区，两个
有效文件最终都位于Zone805306368。终态只匹配空间、有效
文件和占用形态，不声称文件偏移、逐块风险、令牌/预留独占
状态或无限期未来维护完全等价。

这确认了**在本轮无后续有效数据失效的条件下，先清热区减少
一次独立RR，但剩余冷区后续ZC抵消了窗口内的字节优势**。
完整搬运仍为128 MiB，不是净搬运收益证明；也未测量RR/ZC
耗时或性能差异，不能认定时序改变完全没有其他价值。模型必须
计入额外ZC、空间回收完成度及后续目标维护，不能只最大化
RR次数下降。人工指定只用于识别选择的影响，不等于已实现
可实际预测未来维护的RR-aware机制。

### 实验入口、验证与修正记录

新增[`zenfs-controlled-choice.patch`](zenfs-controlled-choice.patch)，
仅修改隔离客机`fs/fs_zenfs.cc::GCWorker()`：同时设置
`ZENFS_ZC_TEST_ENABLE=1`和十进制`ZENFS_ZC_TEST_SOURCE`后，
一次原空间合格且快照覆盖完整的选择只保留指定源区，随后恢复
原批量规则。配置非法记录错误并保持原规则；未启用不改变
默认选择。不扩大原空间候选范围、不改触发门槛、不实现评分。
宿主黄金维护源及原8080实例未应用此补丁。回退实验行为可关闭
实验开关；隔离补丁和实验COW镜像可独立保留或不再使用。

扩展[`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)的
`choice-smoke/cold/hot/verify`，新增
[`tests/run_isolated_choice_guest.sh`](tests/run_isolated_choice_guest.sh)、
[`analyze_controlled_choice.py`](analyze_controlled_choice.py)和
[`tests/test_controlled_choice_analysis.py`](tests/test_controlled_choice_analysis.py)。
两组从同一停止且通过短测的镜像派生，代码、库、初态及计划
一致。静态库和程序编译退出0，程序SHA-256为
`11db10cf9711ffa6394b7968ddac6d0badd75ba4d623b2c3b0bc8142adbed6c1`，
源码为`50a94447f769f58b7696477543ba832a1f7db1fa413e4fa27cfeec17c6793c3e`，
实验维护源为`c49fd3e43c377233c9ebe8554df4e7a845f7849ec9f6a5c95a4499f28a7261cc`。
没有修改FEMU或重链接原`db_bench`，新测试链接实验库，不能
把原`db_bench`的身份误当作新实验库未改变的证明。

首次短测真实执行并通过128 MiB字节比对，但实验等待分支
缺批次汇总，完整解析失败；另发现准备写锁下不能提前消耗
选区机会。保留首版补丁、构建、原日志和错误后修正覆盖检查
及等待空集合汇总。新版短测专门持准备锁12秒：完整覆盖前
实际选中0，解锁后只选择一次，再完成自然ZC及128 MiB校验。
原候选日志中的选中字段仍表示空间资格，实际集合由专属事件
和原生汇总独立核验；仅为旧解析器生成资格汇总视图，不改原
日志或行号，也不把资格数冒充实际选择数。

两组完整执行、初始化器和关闭RR的内容校验均退出0，无维护
错误或失败Reset。最终86项离线测试通过，初始化器/Python
语法、拒绝覆盖、原日志全部哈希和七项前后身份通过核验。
完整核验曾因错误对FULL区无效写指针做地址减法而失败：已
核对FEMU报告实现`zns_wp_is_valid()`分支的`~0ULL`标记，
按FULL容量占满规范化后重新通过；没有修改原设备数据。
修正前短测核验保留，最新使用带“修正”后缀的短测核验。
客机连接也曾被审批服务额度错误中断，用户继续后恢复正常
审批连接，再执行此前未启动的两组负载，没有绕过审批。

原始及修正记录在
[`results/controlled-choice-20261012/`](results/controlled-choice-20261012/)，
汇总见[`等预算选区核验.json`](results/controlled-choice-20261012/等预算选区核验.json)，
简要成本见[`两组完整成本核验.log`](results/controlled-choice-20261012/两组完整成本核验.log)。
四个本轮新data盘COW镜像均保留，核实8 GiB、32完整空区及
未挂载后才初始化。两组并行仅做功能/成本观察，不评价并发
性能；归档核验后正常关机，原8080仍运行。每个实验只删除
自己生成的384 MiB无效模式载荷，可由源码重建；原始镜像、
既有父镜像和用户已有数据未修改，易失ZNS载荷关机不保留。

下一步可在这套同回收要求的配对框架内只改变有效数据后续
失效时序，验证“推迟冷区复制，等待其自然失效”是否能改变
完整账目。它仍是需真实实验验证的假设；本轮结果不证明净
收益，也不证明该课题无收益，尚不确定最终公式或在线预测器。

## 2026-10-12：同失效计划下的等预算选区，冷热各完整一轮

本轮继续第二阶段的收益验证，不增加最终评分公式、权重或在线
预测器。只给上一轮配对框架增加同一份后续冷文件失效计划，
不修改ZenFS核心、原GC20%触发与空间门槛、主机RR机制或阈值。
沿用隔离客机的默认关闭单次指定选区入口；只在本实验显式启用。

### 固定输入与真实执行

两组仍是8 GiB/32 Zone、每区256 MiB、每区64物理块，阈值4096。
24个满区填充文件，两个候选各有效64 MiB/无效192 MiB；热区
最大读数3584，冷区最大448，读数总和均3584，初始均未达阈值。
首次仅选一个原空间合格且覆盖完整的Zone，真实复制64 MiB并
Reset一个源区；两组首次ZC后空闲均960 MiB，净增192 MiB。

测试程序在准备载荷之前声明`expiry-plan.csv`：两文件各按32次/秒
发送24秒，共各768次真实4 KiB业务读取，观察窗口40秒；窗口后、
追加写入前，删除冷组3的64 MiB有效文件。两组都执行该删除，
随后都写入相同512 MiB新文件，不额外读，不直接调用维护函数。
独立校验进程关闭RR和单次选区入口，逐字节比对剩余热文件64 MiB，
并以`IsNotFound()`确认冷文件不存在。全部阶段实际退出0。

### 本轮真实结果

| 人工首次选择 | 初始ZC | 后续RR | 后续额外ZC | 总维护搬运 | 终态空闲 | 终态有效数据 |
| --- | --- | --- | --- | --- | --- | --- |
| 冷区优先 | 64 MiB | 1次/64 MiB | 0 | 128 MiB | 640 MiB | 6720 MiB |
| 热区优先 | 64 MiB | 0 | 0 | 64 MiB | 704 MiB | 6720 MiB |

冷区优先路径已经把冷文件复制到迁移目标；热源随后RR，热文件
也进入同一目标。冷文件失效时该部分区仍含有效热文件，不能
直接Reset，留下64 MiB无效占用。热区优先路径则保留原冷源，
删除其最后64 MiB有效数据后，`ZenFS::DeleteFile()`调用的
`ResetUnusedIOZones()`将源区正常Reset，**没有复制有效数据**。
该Reset不是一次RR或ZC搬运，诊断事件独立记录，两组原冷热源
最终均成功Reset一次。

两组终态满区可回收量都为0，但不能解读为都无无效占用：前组
部分区写入128 MiB、有效64 MiB，后组部分区仅写入64 MiB。
核验器保留真实空间差异，并以IO容量减空闲减有效量核对这64 MiB。
本轮热区优先少搬64 MiB且终态多64 MiB空闲，不需要额外制造
写入来抹平终态。两组同回收要求、同未来计划、同追加负载和
同终态有效量；没有跨不同失效计划借用RR统计。

上一轮**无后续失效**时两条路径总搬运均128 MiB；本轮**同计划
失效**时为128/64 MiB。二者分别是各自配对结果，不混算策略收益。
这支持把“推迟复制能否等来数据失效”纳入cost-benefit研究：
不能只比较当前无效比例，也不能只比较热区读数或RR次数。
但未来失效计划由实验预先指定，不是决策时已具备的真实数据库
预测能力；一轮受控差异不是通用50%优化结论，也不是I/O性能改善。

### 修改、验证与安全边界

本轮只修改实验测试程序
[`tests/controlled_zc_test.cc`](tests/controlled_zc_test.cc)、隔离运行器
[`tests/run_isolated_choice_guest.sh`](tests/run_isolated_choice_guest.sh)、
[`analyze_controlled_choice.py`](analyze_controlled_choice.py)、对应
[`单元测试`](tests/test_controlled_choice_analysis.py)和本文。
新增失效模式，原无失效模式和严格终态等价检查保留；新模式显式
使用`--cold-expiry`，禁止混用历史对照。失效文件、计划、时序、
真实Reset、实际搬运、字节校验、七项前后身份和原日志哈希均核验。

新C++程序链接原库编译成功；先通过带12秒覆盖保护的隔离短测，
再跑两组完整负载。91项单元测试、Bash语法、上一轮严格日志回归
及归档核验器独立重放通过；前四项核心身份和静态库与上一轮相同。
本轮没有重新编译FEMU或ZenFS核心，也没有修改其默认行为。

记录位于[`results/controlled-expire-20261012/`](results/controlled-expire-20261012/)，
最终汇总见[`同失效计划选区最终核验.json`](results/controlled-expire-20261012/同失效计划选区最终核验.json)，
身份及成本复核见[`配对身份与成本复核.log`](results/controlled-expire-20261012/配对身份与成本复核.log)。
冷区日志第317行首次ZC、第386行RR、第494行冷文件删除；
热区日志第268行首次ZC、第490行删除、第491行零搬运正常Reset。
归档保留CSV、原生日志、哈希、编译源码与核验源码，旧结果不覆盖。

从上一轮已停机父镜像创建本轮三个新data盘COW子镜像，原镜像和
原8080客机不变。核实未挂载与32完整空区后才初始化；实验只删除
自己生成的384 MiB初始无效载荷及64 MiB计划失效文件，可按源码
重建。归档后三个新客机正常关机，RAM中的ZNS载荷不保留，子镜像
保留。两组并行仅用于功能和搬运观察，不用于性能对比。

下一步可据此收敛首版cost-benefit机制的输入边界：区分当前能
测得的空间/块风险摘要，和需要估计的未来读取/有效量；明确回收
约束及源、目标后续维护的完整账目。尚不确定最终公式、预测方法，
完整逐块遥测需求、阈值校准、真实数据库与性能验证仍待推进。

## 2026-10-12：首版自动单区成本比较，短测通过，完整对照待执行

设备保持8 GiB/32 Zone。新增选择内核
[`model/rrzc_selector.h`](model/rrzc_selector.h)、内核测试和
[`zenfs-auto-cost-prototype.patch`](zenfs-auto-cost-prototype.patch)，
补丁基于上一轮覆盖保护版人工入口，应用到本轮新COW客机的
`/home/femu/rocksdb/plugin/zenfs/fs/`，未改原8080或本地原始源码包。
另新增[`自动运行器`](tests/run_isolated_auto_guest.sh)，扩展原实验
程序、核验器、单元测试；旧模式及结果保留，所有新备注为中文。

### 已接入的验证性机制

- 默认关闭，显式设置`ZENFS_RRZC_MODE=space`或`rr`才启用，
  并要求`ZENFS_RRZC_RECLAIM_BYTES`和`ZENFS_RRZC_WINDOW_SECONDS`。
  本轮配置回收预算192 MiB、估计窗口40秒。配置非法保持原路径，
  禁止与人工指定入口混用。运行器清除人工源地址；测试中的预期
  地址只用于核验，不作为选择器输入。
- 保留GC20%、原空间门槛、RR先于GC与10秒轮询。候选先取原
  合格满区，再要求全部SST后缀、覆盖完整、有空IO目标可容纳
  有效数据，以及单区回收参考量满足预算。空目标是快照前置
  资格，非完整分配证明；实际迁移仍由已有锁、预留和错误路径保护。
- 只在开关启用时采样C1h。稳定同Reset代际、有效量相同、读数
  不倒退、间隔不超过30秒才计算趋势；采用最近两段最大读数
  斜率的较大者外推窗口增长。重置、不稳定、有效量变化或遥测
  错误清除历史/抵扣资格。最大值可能来自不同块，**不是固定
  物理块速率或概率**。风险块分布保留日志，不乘整区RR成本。
- `space`只比较当前复制量；`rr`用趋势判断窗口内是否可能
  到阈值，一次不选RR搬运暂取当前有效量，即冻结有效量近似。
  不读取实验未来失效计划；已达阈值、多轮风险及不可信估计不抵扣。
  有有效部分目标区、目标信息不全或有效区摘要增长汇总达阈值
  时，用保护项取消抵扣，不把保护项当作真实目标RR次数。
- 在满足单区预算的候选中比较“当前复制＋选后RR保护项−不选
  RR字节估计”；其他候选的不选成本视为公共项。同成本优先
  较大回收量，再按地址打破平局。未知预测按空间成本比较；
  覆盖或空目标未齐时等待，单区预算无法满足则退回原批量路径，
  该回退不宣称满足实验预算。这是验证性比较，不是最终论文公式。

尚无动态预算、多区集合规划、未来有效量预测、完整逐块趋势或
目标后续成本模型。冻结有效量不保证是真正可避免成本的下界。

### 已完成与尚未完成

内核25项合成断言、地址/未定义行为检查通过，覆盖成本权衡、
回收不足、覆盖/目标不足、未知预测、抵消收益、整数范围及趋势
失效。泄漏检查因沙箱ptrace限制失败，原错误保留；关闭泄漏
检查后验证其余检查，不冒称泄漏核验通过。95项Python测试、
Bash语法、两类旧人工组原日志回归通过。新ZenFS库、`db_bench`
及测试程序实际编译退出0；FEMU无需重编译。

真实自动短测在24个填充满区与两个候选上执行。每文件先读
2816次，再持准备锁以32次/秒补读768次，形成24秒已发生的历史。
决策前总读数仍各3584，最大热3584/冷448，空间各有效64 MiB/
无效192 MiB。新载荷含决策前定速读取，不能直接与旧人工组混配。
准备锁期间实际选中为空；覆盖完整后，机制自己选择热源
7784628224，窗口增长估计热1280/冷160，不选RR字节估计热
64 MiB/冷0，增量成本估计热0/冷64 MiB。真实ZC复制64 MiB并
Reset一源；随后短窗各读768次、冷文件真实失效及追加512 MiB
完成，实际RR和额外ZC为0，剩余热文件64 MiB完整字节比对且
冷文件不存在校验通过。短测未来观察20秒、估计40秒不一致，
只验证接入闭环，不据此校准预测或宣称性能/通用收益。

完整两组准备采用同新版软件、决策前读取、32次/秒未来读取24秒、
40秒观察、同失效计划和512 MiB追加，分别用`space`/`rr`。
参考组是同预算自动空间单区版本，**不是stock批量ZenFS**。
两个完整新客机已启动并连接；向sudo认证输入发送密码时，审批
服务返回`429 Too Many Requests`，操作未执行，实验脚本尚未
启动。没有本轮完整配对结果，不能借用旧128/64 MiB数据当作
自动机制收益。未绕过审批；最后确认两台在等待认证，中断后的
最新进程状态尚未确认，恢复后先核实再继续或正常关机。

记录见[`results/auto-cb-20261012/`](results/auto-cb-20261012/)，
真实短测见[`自动选区短测最终核验.json`](results/auto-cb-20261012/自动选区短测最终核验.json)。
八项前后身份、原日志哈希、CSV、编译源码/补丁和构建日志保留。
短测客机已归档后正常关机，两个完整COW客机最后确认已启动，
原始镜像和原8080不变。实验只删除自己生成的384 MiB初始无效
文件与64 MiB失效文件，可重建；关机后RAM载荷不保留。
关闭新入口即可回到原空间路径，但尚未在新核心上专项执行
触发ZC的默认路径大载荷回归；独立关闭入口校验模式已配置，
尚待完整对照执行。下一步先恢复审批，完成两组完整对照与
默认路径验证，再看预测误差、重复实验和真实数据库负载。

## 2026-10-12：用户指定50/50简单双因素，完整baseline对照通过

按用户明确要求，本轮只用**当前有效数据量与当前区内块读次数**。
已向用户说明原ZenFS批量选区与单区greedy不同；用户确认两组
每轮只选一个，baseline取有效数据最少的Zone，新机制用50/50。
因此baseline不是未改动stock批量ZenFS，不混淆这个比较口径。

### 机制与代码

对空间资格、完整SST后缀文件覆盖、目标空间与同回收预算检查
保持一致的满区候选，定义两个[0,1]分量：

- 空间分量：`1 - 有效字节 / Zone容量`，有效量越少越优先。
- 风险分量：`min(区内最大块读数 / RR读数阈值, 1)`，风险越高越优先。
- 新机制得分：`0.5 × 空间分量 + 0.5 × 风险分量`，取最高分。
  实现用百万分整数，先归一化再等权，避免直接相加字节和读次数。
  baseline只比较有效字节；同值按地址打破平局，新机制同分也先
  取较少有效量再按地址。没有趋势采样、预测器或未来失效计划。

采用最大块读数是首版简化：当前RR任一块达阈值即按整区刷新，
Zone总读数不能表达此风险；尚未把多个风险块的分布加入评分。
任一有资格候选遥测不可用时，整轮回到仅看有效量，避免不公平
地把缺失风险当作0打分。源Reset稳定窗口校验仍保留，非原子快照。

核心入口默认关闭；显式`ZENFS_RRZC_SIMPLE_MODE=valid|weighted`
及`ZENFS_RRZC_RECLAIM_BYTES`启用。本轮预算192 MiB，GC20%与
原空间门槛、10秒轮询、RR先于GC不变。覆盖/目标未齐等待；单区
预算不可满足则退回原批量路径，该回退不宣称满足实验单区预算。
只有目标前置空间检查，没有完整候选目标规划；实际迁移继续
用原有预留、锁和错误处理。原8080与默认开关均未修改。

新增内核[`model/rrzc_weighted_selector.h`](model/rrzc_weighted_selector.h)、
[`核心补丁`](zenfs-weighted-5050.patch)、内核测试和
[`隔离运行器`](tests/run_isolated_weighted_guest.sh)，扩展现有受控
程序、核验器和单元测试。核心补丁基于覆盖保护版人工入口源码，
只应用于新COW镜像，不套入旧趋势模型。旧代码与结果不删除。

### 本轮真实完整结果

8 GiB/32区、每区256 MiB/64块、QLC参数、RR探索阈值4096。
24个满区填充文件、冷热候选各有效64/无效192 MiB。初始总读数
均3584，热最大3584、冷最大448，均未到阈值；因此空间分量
均0.75，热风险0.875、冷0.109375，等权得分分别0.8125和约
0.429687。baseline同有效量按地址选冷区，新机制自动选热区，
没有注入人工victim地址。两组首次都复制64 MiB、净回收192 MiB。
两文件随后各实际读取768次：32次/秒24秒、观察40秒；不让
有效文件后续失效，两组追加相同512 MiB写入后核算完整维护。

| 策略（各完整一轮） | 实际首次选择 | 全部RR次数 | 全部ZC搬运 | 全部RR搬运 | 总维护搬运 |
| --- | --- | --- | --- | --- | --- |
| 只看有效量的单区baseline | 冷区 | 1 | 64 MiB | 64 MiB | 128 MiB |
| 有效量/块读数各50% | 热区 | 0 | 128 MiB | 0 | 128 MiB |

新机制的第二次ZC在追加写入后处理冷源；两组终态空闲均640 MiB、
有效量均6784 MiB、满区可回收量0，两原源各Reset一次，IO区
形态相同。RR数量确实减少，但其搬运被另一轮ZC替代，完整字节
成本未减少，不能宣称吞吐、延迟或写放大改善。初始有效量相同，
该设备实验主要验证读风险影响平局取舍；不同有效量权衡已用
合成单元测试覆盖，尚需设备实验与重复运行，不外推通用收益。

### 验证、结果与回退

18项评分内核断言、地址/未定义行为检查、98项Python测试通过；
泄漏检查未执行，不冒称通过。Bash语法、拒绝非法参数、核心
补丁干运行及两类历史日志回归通过。新核心库、`db_bench`与
测试程序编译退出0，先完成12秒准备锁保护的真实短测再跑两组。
短测后只修正启动日志的权重标签，避免baseline被通用文字误标
为50/50；排序不变，重新编译，两组同新版。修正前源码/构建
保留并与短测身份核对。完整两组各阶段退出0，独立校验进程
关闭RR和新选区入口，剩余128 MiB完整字节比对通过。

每组八项身份前后不变且相互一致；原日志哈希、归档核验器重放、
初始与终态空间对等、归一化打分和实际选区均核验通过。
记录见[`results/weighted-5050-20261012/`](results/weighted-5050-20261012/)，
汇总见[`双因素完整对照核验.json`](results/weighted-5050-20261012/双因素完整对照核验.json)，
简要复核见[`配对打分与成本复核.log`](results/weighted-5050-20261012/配对打分与成本复核.log)。
baseline原日志第311/319行首次选择/ZC，第388行RR；新机制
第311/319行首次选择/ZC，第612/620行再次选择/ZC，可追溯。

审批恢复后核实上轮两个旧客机未运行负载，并正常关闭保留镜像。
本轮另建三个data盘子镜像，确认全空、未挂载才初始化；归档
后均正常关机，最后只剩原8080。原始镜像、既有父镜像未改。
只删除每个实验自己生成的384 MiB初始无效模式文件，可重建；
关机使RAM ZNS载荷不保留。两组并行只评价功能/迁移，不比较
性能。关闭新入口回到原空间路径，新核心关闭入口的压力负载
专项回归尚未做；独立校验关闭入口已通过。

下一步保持这版简单机制，优先在不同有效量、不同冷热程度下
复测RR数量与总搬运，并做重复实验；暂不增加此前复杂预测模型。

## 简单双因素的连续周期负载（2026-10-12）

本轮按用户要求增加重复维护和延迟统计，只新增负载与只读分析器，
不修改50/50评分、GC20%门槛、10秒轮询、RR先于GC、设备8 GiB
或探索阈值4096。仍比较每轮只选一个区的有效量baseline和等权策略，
不是原始ZenFS的批量baseline。

新增[`周期负载`](tests/periodic_rrzc_test.cc)、
[`隔离运行器`](tests/run_isolated_periodic_guest.sh)、
[`统计核验器`](analyze_periodic.py)与13项合成统计单元测试。
负载为合成ZenFS文件系统业务，`.sst`不是实际数据库SST。
24个256 MiB填充文件保持不变；其余文件每80秒按固定业务计划换代。
冷文件32 MiB，读分散至8个块；热文件64 MiB，读集中至一个块。
前者迁移成本更低，后者有更高读压力，不注入物理victim地址。

| 相位 | 两组相同的业务操作 |
| --- | --- |
| 0秒起 | 上代业务文件到期删除，写新代冷热有效文件和临时文件，各真实写满一个256 MiB区 |
| 10秒起 | 每文件3584次4 KiB读取，512次/秒；此时临时数据仍有效，尚无空间候选 |
| 20秒 | 删除224/192 MiB临时文件，形成冷热空间候选；开始每文件768次后续读取，32次/秒 |
| 44秒 | 后续读取结束，记录空间与C1h遥测 |
| 52秒起 | 写两份256 MiB业务文件，单文件按64 MiB/s定速；不直接触发清理 |
| 80秒 | 记录周期边界状态，继续下一代，不重启、不格式化、不直接重置设备 |

到期删除后自然回收全无效区是真实业务行为，但不能算作有搬运的ZC
或独立RR。该负载有意突出冷热候选竞争，是固定相位周期轮换，
不能外推为随机持续负载、真实RocksDB性能或通用论文结论。
只启动一个对照客机，串行执行两组，每组8个连续周期、640秒，
前2周期预热，后6周期计量；完整维护总账仍包含预热，不隐藏其成本。

延迟来自真实同步ZenFS API计时，读4 KiB、写1 MiB及元数据操作分开，
记录平均/p50/p95/p99/最大值，主动定速等待不计入API延迟，排程迟到
另记；热身读与后续读分别汇总。它不是开放请求队列端到端延迟、
NAND时延或数据库请求时延。两组应核验相同程序身份、实际逻辑轨迹、
业务请求总量、原始日志哈希以及独立校验。最终冷热96 MiB与业务512 MiB
完整逐字节校验，24个填充文件各首4 KiB抽检，不冒称全盘完整校验。

相对周期稳定的筛查在完整对照前确定：预热后至少4周期；相同相位
边界有效量波动不超过32 MiB，空闲和满区可回收量各不超过一个Zone；
每周期ZC次数、RR完成次数、总维护迁移量变异系数各不超过0.25；
至少4个有搬运维护事件。此筛查只说明本负载没有明显周期漂移，
不等于周期内恒定负载或统计意义上的一般稳态；不达标则明确报告。

本轮新增程序编译成功，先完成单周期weighted短测：两次自动ZC共
96 MiB，RR为0，独立608 MiB完整比对和填充抽检通过。原始证据位于
[`results/periodic-5050-20261012/`](results/periodic-5050-20261012/)，
短测核验见[`单周期短测核验.json`](results/periodic-5050-20261012/单周期短测核验.json)。
### 八周期串行对照的真实结果

两组分别完成640秒连续业务，预热后计量480秒；每组实际执行
69632次4 KiB业务读、8 GiB业务写，不包括6 GiB初始填充和最终校验。
评分内核、ZenFS库、负载程序、运行器等八项身份前后不变且两组相同，
实际逻辑轨迹哈希相同。逐轮归一化打分和实际排序重算通过；两组
非空原批量回退均为0，没有通过切回批量策略完成实验。

| 全部8周期维护账目 | 有效量单区baseline | 50/50新机制 |
| --- | --- | --- |
| 实际有搬运ZC轮次 | 12 | 19 |
| 完成的独立RR次数 | 7 | 0 |
| ZC搬运 | 416 MiB | 960 MiB |
| RR搬运 | 448 MiB | 0 MiB |
| 总维护搬运 | 864 MiB | 960 MiB |
| 维护源区成功重置 | 19 | 19 |
| IO区全部成功重置 | 33 | 33 |

两组各14次非ZC/RR数据区重置是业务到期后的自然回收，不能混入
ZC或RR次数。元数据区挂载重置各1次，单独记账。期末两组有效量
均6752 MiB、空闲576 MiB、满区可回收量0，IO形态相同：26个满区、
2个空区、1个写入192 MiB的关闭区。容量减空闲减有效量为96 MiB，
是未计入满区回收口径的占用无效字节，不能因回收量0说全盘无无效数据。

| 预热后6周期的同步API延迟 | baseline均值 / p99 | 新机制均值 / p99 |
| --- | --- | --- |
| 所有4 KiB读（包含阈值以下热身） | 129.272 / 231.935 微秒 | 126.787 / 228.464 微秒 |
| 后续业务4 KiB读（单独统计） | 207.732 / 2969.975 微秒 | 215.836 / 2998.218 微秒 |
| 1 MiB写 | 3349.655 / 12389.963 微秒 | 3384.984 / 12532.872 微秒 |

总体读统计主要由热身读构成，不能掩盖后续读阶段。p50/p95/最大值、
元数据操作延迟、排程迟到、每周期延迟及维护明细均在
[`周期完整对照核验.json`](results/periodic-5050-20261012/周期完整对照核验.json)。
预热后两组各52224个读样本、6144个写样本；业务读204 MiB、业务写6 GiB。
预热后baseline为10次ZC/5次RR、672 MiB搬运，新机制15次ZC/0次RR、
768 MiB搬运。全部周期结果保留，不只截取对新机制有利的阶段。

新机制通过事先确定的相对周期稳定筛查；baseline边界空间和总搬运
筛查通过，但每周期ZC/RR次数波动超过0.25，因此**尚不能宣称两组都
完成了正式稳态实验**。只做了一次串行配对，不给小幅延迟差异附加
统计显著性，也不能声称吞吐、尾延迟或总成本改善。探索阈值4096
和受控周期负载都不是经过校准的正式QLC实验条件。

本轮RR从7降到0，但总搬运增加96 MiB、约11.1%，维护源重置总数
未下降。原因有原始日志依据：预留区残留历史无效字节，尾部不足时
替换/封闭原区，使刚迁移的数据可能再次成为清理对象。baseline在
周期2/4/6各额外搬32 MiB冷文件；新机制在周期2/4/6各额外搬64 MiB
热文件。新机制周期2日志的实际选择/迁移记录可看到热文件被连续
两轮ZC搬运；baseline周期2还在RR前通过第二次ZC清热区，不能声称
每个周期baseline都会触发RR。这不是程序失败，而是当前首版评分
未考虑目标尾部及重复搬运成本的实验现象，不能只看RR数量下结论。

新增程序编译退出0；13项新增统计测试加98项已有Python测试共111项
通过，Bash语法检查与真实短测通过。两组初始化、负载、独立校验和
运行器均退出0，无维护错误。校验阶段关闭RR与简单选区入口，无额外
搬运。完整原日志、操作时延、配置、身份和哈希已归档。SSH控制连接
到期后仅重新认证取回已完成结果，没有重复启动或延长原实验。
本轮三个data盘子镜像均保留，归档后正常停机；原8080与原始镜像未动。
只删除了本轮生成的到期业务模式文件；关机后RAM ZNS载荷不保留。

下一步应先用更长窗口和独立重复确认维护次数/延迟的波动，再研究
迁移目标尾部、重复搬运是否应纳入cost-benefit；本轮**没有**擅自
修改50/50公式、基线选区、RR调度、启动门槛或默认行为。

客机新增负载的编译命令为：

```bash
cd /home/femu/rocksdb
g++ -std=c++20 -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude /home/femu/rrzc-periodic-tests/periodic_rrzc_test.cc librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o /home/femu/rrzc-periodic-tests/periodic_rrzc_test
```

## 连续滚动读写负载与维护次数扩展（2026-10-12，已完成）

用户要求增加GC/RR次数，并测试读热点与更新旧版本同区的条件。本轮
只改实验负载，不改50/50评分、有效量基线、GC20%、RR调度或设备大小。
新增 `tests/stream_rrzc_test.cc`、`tests/run_isolated_stream_guest.sh` 和
`tests/run_stream_pair.py`，以及 `analyze_stream.py` 与对应单元测试。

负载为直接ZenFS文件API，不是真实RocksDB SST：每30秒写512MiB，
新一代包括冷区16MiB读锚点与240MiB更新版本、热区32MiB读锚点与
224MiB更新版本。新版全部写完才删除上一代更新文件，使原读热点
和失效旧版本确实同区；读锚点寿命120秒，业务读写并发。固定读计划
先读到阈值以下，再在更新后继续读。两组使用相同请求计划，不人为
调用GC/RR/Reset，不按选区结果改变热点。固定填充20个Zone；设备
仍8GiB、32个256MiB Zone，探索RR阈值4096，回收预算192MiB。

两次失败预试均保留，不能作为成功性能结果：23个固定填充区时，
第4代附近耗尽普通空目标区，出现 `Zone allocation failure`；减少为
20个填充区后，GC/RR把有效数据迁入暂时关闭的当前部分热区，导致
同代版本跨区。原错误、空间记录、维护日志、实际源程序和编译状态
分别归档于 `results/stream-5050-20261012/failed-23-fillers/` 和
`results/stream-5050-buffer20-pilot-20261012/`，两个镜像保留并正常停机。

修正负载通过正常业务写句柄保持每个更新文件的Zone，写满才关闭；
不直接锁Zone，也不更改分配器或迁移目标选择。冷热布局检查保留，
没有删除检查绕过失败。每代更新文件由29个16MiB文件改为两个
240/224MiB文件，总写入量和有效/失效数据量不变；元数据工作量变化
意味着不能把前两次失败预试与修正版本合并成性能样本。

持写句柄的240秒预试中，baseline负载与只读校验完成，但weighted
在首个版本块写入前的58.6ms节流空档仍被迁移借用热区，布局检查
再次报错，两组不能合并为成功对照。该轮归档于
`results/stream-5050-heldwrite-pilot-20261012/`，镜像保留并正常停机。
还定位到已删除文件再关闭写句柄会使旧ZenFS日志访问空链接名列表；
当前实验程序改为持写锁改成非SST退役名、关闭、最后删除，未修改
ZenFS核心。首个版本块的句柄/缓冲提前准备，连续接锚点末块写入，
去掉交接节流空档；两组使用相同的固定微小突发和后续节流计划。

修正后两组各180秒预试 `results/stream-5050-handoff-pilot-20261012/`
均完成并通过独立只读数据校验。原监督进程的统计尝试因使用多次
设备查询开始时间对齐日志而报错，错误保留；分析器改用真正同步
记录的更新提交时钟，重新只读核验通过，未重跑或改动原日志。
两组软件身份与每线程完整请求轨迹相同，逐轮评分复核通过，非空
批量回退0次，终态空间相同。baseline成功ZC/RR为4/4，等权组为
7/1；两组迁移均192MiB，维护源重置均8次。预热60秒后业务读API
平均199.213/197.935微秒、写API平均3.480/3.424毫秒，排程至API
完成的读响应平均1.364/1.310毫秒、写响应平均3.654/3.613毫秒。
这仍是短测，不是稳态或显著延迟收益证明；RR减少未降低总搬运。
完整核验见该目录 `完整对照核验.json`，保留原监督状态的核验失败
记录以便追溯。源码编译退出0、122项Python统计测试通过。

已启动 `results/stream-5050-long-20261012/` 两组各5400秒长测，
从同一已停机、包含通过预试程序的父镜像派生，各组新命名空间，
结果目录不覆盖。自动串行运行valid/weighted、校验、归档、正常
停机和输出核验JSON/指标CSV；当前进度见 `状态.json`。密码仅驻留
监督进程内存，不写文件或命令行；未修改SSH授权文件/服务。原始
8080客机和原始镜像未动。两组现已完成、归档和核验通过；剔除前
600秒后baseline成功ZC/RR为193/159，50/50组为335/10，总搬运为
8192/8880MiB，平均排程至API完成读响应为1.818/1.400毫秒，写响应
为3.747/3.728毫秒。baseline通过相对稳定筛查，50/50组的稀疏RR
窗口变异系数未通过；两组终态空间不同，不宣称总维护成本降低。
逐文件核算预热后首次搬运均7680MiB，重复搬运为512/1200MiB，
多出的688MiB来自额外重复搬运；原始日志和汇总均保留。

新增分析器事先规定五分钟
窗口、正式运行预热600秒、至少六个完整计量窗口；观察窗口平均
空闲/有效/满区回收量波动是否不超过一个Zone，ZC/RR/搬运量的
窗口变异系数是否不超过0.25。维护规模目标是计量区间baseline
至少100次成功ZC及100次RR，新机制至少100次成功ZC，不强求新机制
也触发100次RR。规模目标与相对稳定筛查分开报告，不保证达到。

统计包含全程和预热后GC/ZC、RR、迁移量、主机IO区重置/自然回收，
以及业务读写API平均/p50/p95/p99/最大延迟、排程迟到和排程至API
完成的响应延迟；后者含业务线程排队，不是NAND延迟。逐轮重算
真实评分，核验每线程完整逻辑轨迹、软件身份与日志哈希，保留
终态未回收空间账目。预试完成并独立只读校验通过前不启动长测；
两组各90分钟的规模预算依据短测每30秒约一次baseline ZC/RR估计，
扣除预热后预计有超过100次的观察机会，不把该估计当作实际结果。

## 空间80%／读风险20%实验（2026-10-13，已完成）

用户明确要求先比较80/20，空间分量占80%，最大块读风险占20%。
本轮只增加实验权重配置 `ZENFS_RRZC_SPACE_WEIGHT_PCT`，未设置仍
为50/50；选择分数为 `(空间分量×80＋风险分量×20)/100`，原分量
归一化、同分排序、覆盖/目标资格、192MiB预算、GC20%及先RR后GC
调度均不变。baseline仍按有效量最小单区选择，不是stock批量策略。

修改 `model/rrzc_weighted_selector.h`，新增小范围补丁
`zenfs-space-weight.patch`；扩展滚动运行器和统计核验器，权重写入
配置CSV并与实际启用日志核对，逐轮重算80/20分数和实际选择。50/50
旧日志字段及解析仍兼容，80/20使用“加权”字段避免误标为等权。
历史日志、原始镜像和已有50/50结果不覆盖；本轮改动均有中文备注。

构建子镜像为data盘 `stream-8020-build-20261013-build.qcow2`，
仅在该新子镜像备份后应用补丁，静态库增量重编退出0，只重编
ZenFS主对象和已有构建版本对象。实际源码、旧源码备份及编译日志
可从该镜像追溯，本轮构建归档见
`results/stream-8020-build-20261013/build/`。36项C++合成评分断言、
124项Python统计测试通过，已有50/50短测重新只读核验通过。
最初新增合成测试把阈值满风险候选误设为80/20应落选，断言失败；
按实际公式修正合成输入为半阈值风险后通过，未修改评分绕过断言。
补丁首次按重算hunk行数应用，随后修正本地补丁行数并通过标准
`git apply --check`；仅补丁元信息变化，不改变已编译代码。

串行预试 `results/stream-8020-pilot-20261013/` 两组各180秒，均已
完成、归档和核验通过，客机正常停机；不因对话中断而重复启动。
baseline成功ZC/RR为4/4，80/20组为7/1，两组迁移均192MiB；软件
身份、每线程请求轨迹和终态空间一致，逐轮80/20评分复核通过。
预热60秒后平均API读延迟197.671/196.803微秒、写延迟
3.473/3.475毫秒；平均排程响应读延迟1.485/1.307毫秒、写延迟
3.666/3.670毫秒。短测规模不足，不据此宣称稳态或性能收益。
业务负载源程序与此前50/50相同，只重新链接新库并上传带权重
配置的运行器。
设备仍8GiB/32个256MiB Zone，RR探索阈值4096，填充20区，每30秒
写512MiB，读锚点寿命120秒；原8080客机未动。已启动
`results/stream-8020-long-20261013/` 两组各5400秒的串行长测，
新baseline与80/20使用同一新版程序及同一已停机预试valid父镜像。
该目录 `状态.json` 已记录两组完成、归档和核验通过。自动归档、
独立校验、核验和统计流程不变。剔除600秒预热后，baseline的ZC/RR
完成次数为193/159，80/20为337/10；总维护搬运8192/8976MiB。
首次搬运均7680MiB，重复搬运512/1296MiB，因此多出的784MiB来自
重复搬运。两组终态空间不同，不能宣称综合维护成本降低。
平均API读耗时为198.489/195.741微秒，写耗时为3.511/3.505毫秒；
计划执行时刻到API完成的平均读响应为2.445/1.648毫秒，写响应
为4.217/3.760毫秒。响应差异主要来自排程迟到，不是纯设备延迟；
这仍是串行单次配对，未做独立重复或统计显著性验证。
旧50/50长测仅作为历史参考，不冒充同软件身份的新配对baseline。
80/20不保证选区与50/50不同或一定改善，结果、终态空间及重复搬运
均需依据实际长测记录；未执行最终cost-benefit改造或RR调度改造。
对旧50/50长测371轮可重算候选快照做了离线反事实检查，在相同
候选集合上用80/20重算，排序均未改变；这不是80/20真实运行结果，
也不代替新长测。实际80/20启用权重已由原始日志确认。

## 2026-10-13：分阶段纳入版本控制

按设备读取计数与C1h接口、主机RR、分配诊断与回退、迁移预留及
尾部保护、维护诊断、受控实验与历史成本探索、50/50选择、周期与
滚动负载、80/20权重实验的依赖顺序补充提交。提交时间采用实际
提交时间，不伪造早期开发日期；50/50内核及测试使用已归档源码
作为该阶段的索引版本，随后提交现有可配置权重版本，不覆盖工作文件。

`results/`约3.4GiB的原始日志、请求轨迹和源码快照留在本地，镜像及
构建产物不提交。明确选入版本控制的证据仅为50/50与80/20长测的
`指标汇总.csv`及`完整对照核验.json`；汇总内保留运行配置、软件身份、
轨迹哈希和终态空间账目。仓库副本不包含完整原始数据，不能仅凭
这些汇总重新执行原始日志核验；须结合本地实验目录。

本轮不改变RR、GC触发、victim selection或预留区管理，也未启动
设备实验。版本控制前的FEMU构建检查、124项Python单元测试、
25项历史选择内核断言、36项双因素内核断言、11项分配边界测试、
32项迁移资源与RR源区测试、18项维护诊断测试及Shell语法检查通过。

## 安全边界与下一阶段

原始镜像 `/home/data/xwz/femu.qcow2` 未修改。后续预试子镜像位于
`/home/data/xwz/rrzc-baselines/`，原始日志保存在本目录 `results/`；
前期源码改动保存在 `build-femu/rrzc/guest-overlay.qcow2`，RR 功能烟雾测试
保存在其子镜像 `build-femu/rrzc/rr-test-overlay.qcow2`。FEMU 的 ZNS 存储为易失性，关闭客机
后设备数据不会保留。已检查客机中的 `init.sh`：它会删除 `/tmp/zenfs` 并格式化
`/dev/nvme0n1`，因此**没有执行**该脚本。

当前 RR 原型仍有边界：非 `.sst` 文件或快照未覆盖全部有效数据时会延后，
不强制重置；一次检查只处理一个 Zone。迁移字节已经分项统计，但尚未测量
设备级写放大、每次重置的耗时或物理块擦除总数，尚未完成正式 RR 感知的 ZC
选区策略。尾部修复后的同新版 45 万条 RR 一轮完整运行、一次并发
删除中止，随后复跑再现容量不足并人工结束；共同降至 40 万条后
两组均完整完成，RR 开启组同配置两次完整运行且未记录容量失败，
第二次有一次并发删除中止；ZC-only 未实际清理，仍不是普遍稳定的
性能对照。已有离线观察留下两条近期候选/RR 关联，但均为有效数据
接近整区、已达阈值且未满足原空间门槛的区，不能作为选区收益证明。
默认关闭的主机 Reset 与资格诊断已实现，并通过小载荷、数据库烟雾
及一轮带诊断的 40 万条完整 ZC/RR 负载；同代际近期关联仍不等于
选区收益。受控冷热读三档前置测试已完成，正式阈值尚未校准；
随后完成两轮固定四格的分阶段ZC及后续RR观察，以及两轮同进程
开启RR/ZC的同步受控观察；后者总搬运量没有减少，不是收益证明。
已完成两个阈值以下风险/失效时序条件各一轮，观察到相同候选
有效量对应不同未来RR搬运量，仍非选区收益证明。下一阶段保持
GC20%和当前机制作为基线。已完成独立候选成本表与静态空间
核验，及固定未来窗口三档读取计划各一轮，观察到目标共享布局
再次RR，但尚非策略收益。候选准入和回收约束属于后续模型
设计，不把原门槛当作不可突破边界；已完成等未来业务读量、不同
节奏两组各一轮，两组均RR一次64 MiB，仅实际维护时机不同。
已集中整理模型输入，并完成一次人工指定的等首次回收预算
配对对照：先清热区消除了本轮独立RR，但补齐空间后额外ZC
使两组总搬运均128 MiB。它非原批量策略或最终模型对照。
随后同冷文件失效计划下两条路径已完整运行，总搬运128/64 MiB，
终态空闲640/704 MiB；这只是受控条件差异，非最终策略收益。
此前自动成本预测版只完成短测，未补做其完整配对；按用户新要求
改成有效量/块最大读数各50%的简单版本，完整对照RR为1/0，
总搬运均128 MiB。下一步保持简单版，做不同有效量/冷热及重复
验证、默认路径回归，再在后续校准
多档阈值，确认可避免的未来 RR，
研究空间回收与迁移成本权衡；
不把短测当作性能改进证据。
