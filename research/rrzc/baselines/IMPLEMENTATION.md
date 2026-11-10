# 独立相关工作实现记录

## 2026-11-06：来源冻结与隔离起点

用户选择同项目管理、三个独立源码/构建目录，外部对照为 CAZA、完整 OAZA-GC、ColdMap。FineRR-ZNS 不纳入此次放置/选区主对比。不复制三个完整 FEMU 项目，不覆盖旧机制、不修改已归档文件。

本轮新增文件均位于 `research/rrzc/baselines/`。`isolation.py` 已生成 `workspaces/initial-20261106/{caza,oaza_gc,coldmap}/`，各有独立 `src/plugin/zenfs/fs/`、`build/`、`patches/`；来源为已交付第二阶段独立源码。该来源仍含原机制代码，当前是待适配的冻结起点，不能称为已实现的外部算法或中性基线。

共同来源同时核验 `副本身份.json`、`交付文件SHA256.json`、全部已登记源码、旧程序。旧程序 SHA256 为 `2a00ce36f993ad068d9da030d0d46143b16591a5fde7f81880ab62874ea1fa5d`；原库登记 SHA256 为 `fe0123f63993b7a7a823bd114729bdf281696eb45e3d619034396bda8316c37b`。本轮未取得原库文件字节，因此这里只引用已登记库身份，不声称本地主机重新验证了该库字节。

原机制保护清单覆盖现有 `hw/femu`、`research/rrzc/model`、`research/rrzc/tests` 的相关源码，以及 rrzc 顶层分析脚本、补丁和说明；包含当前未跟踪代码。准备前后校验这些已登记文件及旧程序未变。后续若有其他任务改动其中某个文件，核验会指出身份变化；不能据此直接断言该变化由本入口造成。

共登记1243个保护文件，每份起始副本72个源码文件；ColdMap上游登记1955个文件。收尾检查发现 `research/rrzc/tests/recover_placement_archive_host.py` 从 `5eda6757fd670fc31d43c480bb1a9d7eb4f61756838b3c2597533ba7569366d8` 变为 `c17fa94e83d39407bd43fcb8059c7fc58e54cc3c63e78eca4538e84501f83fc4`。本轮仅写baselines目录，没有写该脚本；不能据此判断具体修改者。保留原清单并另存 `收尾复核-20261106.json`：`passed=false`、`source_trees_valid=true`。严格核验继续以退出码1报告差异，不把并发工作区变化包装成全部保护通过。

每份副本通过独立普通文件复制；检查 inode 和链接数，拒绝链接共享。拒绝输出覆盖、输出进入原源码/既有结果目录、清单路径越界、未登记源码及未核验构建产物。当前核验对象是已登记文件，不宣称对整个服务器或 Git 忽略的全部数据进行了快照。

## 外部材料

ColdMap 作者仓库固定在 `e03c70760371b540964dac8b3a0c2596c06cf418`。逐文件 SHA256、Git blob 与工作树一致性已核验，详见工作目录 `ColdMap上游身份.json`。仓库自带 RocksDB 版本为 7.4.4；尚未确认与当前实际客机源码版本兼容。只读取其源码，没有执行 `pullandcompile`、`push` 或其他上游脚本。

CAZA 作者公开 PDF 已下载为 `materials/caza-hotstorage2022.pdf`，已读取第4节算法与fallback。原作者站下载发生 TLS 中断，改用作者公开 GitHub Pages 地址成功。OAZA 论文发行方文本已核对，但本地完整 PDF 下载返回 HTTP 403，尚未取得可逐公式/伪代码核验的完整本地版本；不能根据缺失的公式图片猜写算法。GitHub 文件清单 API 同样返回403，改用固定提交的只读克隆取得 ColdMap 文件。

CAZA PDF SHA256为 `2e921198195f231045ce1889a76a5a375d5215b01bf68395deb0fdc400ae5306`，来源记录见 `materials/来源.json`。

## 已确认的接线位置与差异

下面的路径以 `materials/coldmap-upstream/rocksdb/` 为起点，均已在固定提交实际查到；它们是移植参考，不是当前项目已有接口。

|作用|作者实现位置|当前需要处理的内容|
|---|---|---|
|SST层级与Key范围发布|`table/block_based/block_based_table_builder.cc` 中的 `SetMinMaxKeyAndLevel` 调用|需要核对当前TableBuilder版本与写出时机|
|文件接口传递|`include/rocksdb/file_system.h`、`file/writable_file_writer.cc`|作者增加虚方法与写出调用；不能混用旧头及旧对象|
|等待信息后写出|`plugin/zenfs/fs/io_zenfs.cc` 中 `CAZAAppend`、`CAZAFlushSST`|作者缓存SST写入，随后实际分配/写出；会改变内存及I/O时机|
|CAZA参考分配器|`plugin/zenfs/fs/zbd_zenfs.cc` 中 `AllocateCompactionAwaredZone`|不是已核验的原CAZA完整复现|
|ColdMap文件冷度与模拟|`plugin/zenfs/fs/fs_zenfs.cc` 中 `CalculateHorizontalLifetimes`、`ReCalculateLifetimes`、`PredictCompaction`|须核对当前Compaction选择规则、元数据一致性、循环进度和有界退出|
|ColdMap步数更新|`plugin/zenfs/fs/zbd_zenfs.cc` 中 `UpdatePredictCnt`|已完成实际原函数主机验证，尚未接入本项目|
|ColdMap受害排序|`plugin/zenfs/fs/fs_zenfs.cc` 中 `CBZC3` 等分支|上游含多个变体；需确认论文对应配置，不能任选分支冠以论文名称|

CAZA原文第4节明确按重叠SST数量对Zone排序，并有“无效区清理/空区、同层近Key范围、LIZA”回退次序。ColdMap仓库的参考CAZA分配器按 `extent->length_` 累计 `zone_score`，且存在不同层级处理。不能直接拿这一派生分支当原版CAZA，也不能把原文回退简单替换成任意普通分配。

ColdMap README称Zone冷度按大小加权，但当前 `ReCalculateLifetimes` 可见实现是每个extent增加 `total_lifetime` 与 `file_count`，相应 `CBZC3` 分支再计算算术均值。该差异需要与正式论文及实验变体核对，不能在移植时静默“修正”为另一公式。

现有机制的 `ZoneFile::AllocateNewZone`、`ZonedBlockDevice::AllocateIOZone/GetBestOpenZoneMatch`、`ZenFS::GCWorker/MigrateExtents` 可作为接线审查起点。不能把目前Flush来源W当作三项方法所需的真实层级/Key范围；不能只在Compaction完成后补记信息并称为分配前已知。

## 本地验证及证据边界

隔离与篡改检测13项测试通过。首次测试暴露测试工程缺少AGENTS.md，以及空路径段未拒绝两项问题；分别补齐临时夹具、收紧路径校验后重新通过。未通过结果未被记成通过。

ColdMap `check_adaptive_window.py` 从固定提交提取 **未修改正文** 的 `UpdatePredictCnt()`，以同名字段类作为后端，严格编译并运行实际函数。覆盖静态模式、首次默认/显式值、三轮窗口移出、整数向下取整、计数清零与结果为0。函数 SHA256：`f59bea249d2a2035d47b46caceccfb0b41565bf3e8d1f7dee1e0e894ec0127ac`。

首次编译成功，运行因LeakSanitizer检测到沙箱ptrace而失败，原输出保存在 `coldmap/native-window-check/run.log`。取得沙箱外运行许可后，以独立新输出目录重编重测，`-Wall -Wextra -Werror`、ASan、UBSan通过，详见 `coldmap/native-window-check-unsandboxed/result.json`、`compile.log`、`run.log`。没有关闭消毒器或覆盖原失败日志。

上述原函数夹具不验证线程安全、整个压实模拟、完整选区、ZenFS集成、重挂载或性能收益。三项工作均保持 `implementation_complete=false`、`runnable=false`。没有完整RocksDB/ZenFS新构建，没有启动新客机/监督，没有新设备实验或性能数字。

## 下一步实现边界

1. 取得当前共同软件版本的完整RocksDB源码身份，确认分配前传递SST范围所需的最小接口；新对象统一构建，不混入不同ABI的旧对象。
2. 对照CAZA原文完成分配与fallback，再接真实设备路径；不能以参考仓库的不同评分替代。
3. 补齐OAZA/OAZA-GC公式与伪代码证据，完整保留动态回收数量、成本及选区。
4. 确认ColdMap论文对应的上游变体、冷度聚合与模拟配置，再移植；使用冻结快照逐项核对中间结果。
5. 逐项区分公共安全约束与本机制实验限制。现有单区回收、候选有效比例和额外预算不能未经分析就套到OAZA-GC等方法上。

正式实验入口尚未创建；后续仍须接入既有镜像/结果生命周期、输入与起点核验、逐笔账目、串行关机收尾。不增加Greedy独立重复组作为启动门槛。

## 2026-11-06：CAZA 独立适配与主机验证

本阶段只修改 `baselines/`。当前交付是按 CAZA 论文第4节实现的适配原型，尚非已经设备验收的完整复现。OAZA-GC、ColdMap 的完整实现状态没有变化。

### 匹配的完整源码

`export_source_host.py`、`export_source_guest.py` 使用临时系统客机，只读核验并导出当前软件；不接入模拟 SSD，不改原静态库。第一轮因发现未跟踪的 `zenfs.dbg` 超出源码文件限制而失败，保留错误归档；收紧源文件筛选后第二轮成功。两轮均完成归档、正常关机及临时镜像回收。

成功来源：`/home/data/xwz/rrzc-baselines/joint-related-source-v2-20261106-results/joint-related-source-v2-20261106/`。包含2227份源文件、43,274,594字节；RocksDB 10.7.0，Git HEAD `d87e598f70c960e3de2ea1984111f4fa35cbfee6`；ZenFS HEAD `919c2ebbcdc170525a9abffb8b61a3795b1e6ae5`，另有工作树修改，已逐文件记录，不能称为干净上游版本。

- `source.tar.gz` SHA256：`99eb8136a601d6283226e12e4d2ef151a422732a0ff966e318868345d02ab879`
- `source_identity.json` SHA256：`881b4ed82a075312d3362f8ead369f7a7585f93f7ff5f18a64a87998be6408f1`
- 本次实际核验原库字节，导出前后均为 `fe0123f63993b7a7a823bd114729bdf281696eb45e3d619034396bda8316c37b`。

### CAZA 实现内容

`caza/prepare.py` 从上述逐文件核验来源生成完整独立树，再覆盖已核验的第二阶段 ZenFS 冻结源码及本阶段补丁。当前副本是 `workspaces/caza-v4-20261106/`，2244份文件；`caza.patch` 及 `overlay_identity.json` 可审查和核验。生成器拒绝覆盖已有输出，源码锚点不唯一或来源哈希不符时终止。

|模块|已实现内容|待验证的实际路径|
|---|---|---|
|`caza/policy.h`|对目标层 L 的新 SST 统计 L+1 重叠 SST 数量；同一 SST 在一个 Zone 的多个 extent 只计一次；按数量降序|真实分配选中的 Zone 与统计输入是否一致|
|`caza/bridge.h`|发布最终 Key 范围、已提交的 LSM 文件集合及文件物理 Zone 代际映射；线程局部作用域隔离请求|Flush、Compaction、删除、迁移及重新挂载的集成一致性|
|TableBuilder 与 DBImpl 接线|在 TableBuilder 完成时发布最终范围；在安装 SuperVersion 时发布已提交文件层级与范围|真实 RocksDB 回调时机、元数据路径名、输出层级|
|`caza/writable_methods.inc`|元数据完整前有界暂存；之后使用原生 buffered/direct 写路径重放；错误不重复重放，关闭时释放所有权|真实 Sync/Truncate/Close 及故障注入后的内容和引用|
|`caza/zenfs_adapter.inc`|依次尝试重叠区、全无效区清理及空区、同层最近 Key 范围、原 LIZA；重新检查代际、占用、容量和内容类型|真实 Open/Active token、跨 Zone 写入与原分配重试路径|

`ZENFS_RELATED_WORK=caza` 是独立模式；检测到原协同池及已列举模型开关时拒绝混用。正式运行仍应使用明确的干净环境和共同安全配置，不能把“检查部分冲突开关”宣称为已核验所有外部配置。

当前适配边界：单 DB、默认 CF、定长 Bytewise 点 Key，不支持时间戳与范围删除；同层距离采用定长大端字节数值差，论文未规定任意比较器下的距离，属于明确的适配约定。每个 SST 最多暂存256 MiB，超过后返回失败，不悄悄退回其他策略。暂存会增加内存、改变设备写入时机；正式性能对照需要记录和核算这些开销。相同计数时按 Zone 地址及代际稳定打破平局，原文没有规定此顺序。

### 真实完成的验证

`caza/check_host.py` 检查8个相关源文件（ZenFS五单元、TableBuilder、DBImpl、Flush）和测试客户端的主机语法，另编译并运行策略、桥接、写入重放、分配器四组测试。分配器与重放测试直接编译适配 `.inc` 正文，但其 I/O/Zone 后端是测试替身，**不等于真实设备验收**。

主机没有 libzbd 及压缩库开发依赖，语法检查使用 [libzbd 官方2.0.4公开头文件](https://raw.githubusercontent.com/westerndigitalcorporation/libzbd/v2.0.4/include/libzbd/zbd.h)，未安装系统依赖，未启用可选压缩提供者。检查通过不能替代客机原配置的完整编译和链接。头文件 SHA256：`f24035f4c734358e61e97e0c752bb963b8754d50c7ce0e8d34b332b071019412`。

验证日志在当前副本的 `host-check-v2/`；8个相关源文件及客户端语法检查、四组独立测试均通过，检查结束核验源文件与测试材料身份不变。独立隔离及源码筛选14项 Python 测试通过。早期生成器曾因 CloseInternal 中两处相同错误处理语句而拒绝不唯一锚点，改用带 DataSync 上下文的完整锚点；没有绕过唯一性校验。最终 v4 的四组 ASan/UBSan 测试也通过，见其自身 `asan-ubsan-v1/result.json`；没有用旧副本的通过代替新副本验证。

### 上一轮记录：完整构建与设备验收尚未完成

`caza/run_build_host.py`、`caza/build_guest.py` 已准备好串行编译8个相关单元及新客户端，核验链接映射拒绝混入对应旧对象，并在前后检查原库不变；Flush也独立重编，避免继承旧实验中的专用Flush对象。它们复用既有 ImageLease、结果保存、独立监督、正常关机及镜像回收机制。计划只用4 GiB系统内存、2 GiB新增磁盘预算，不接模拟SSD。

尝试启动时资源前置检查发现正在运行的 G0 放置消融实验（当时 PID 2382381，8101端口），按默认串行规则终止。没有绕过检查、叠加客机或关闭它。完整客机编译/链接、POSIX实跑、真实ZNS放置命中、Compaction、重挂载、GC/RR账目及性能均仍未验收。

收尾复核时，G0客机已不再是当前目标，8101上实际运行的是 `joint-controlled-placement-exit-fix-v2-20261106`，PID 2388374，当时RSS 4,745,772 KiB（约4.5 GiB）。继续按“有本项目客机，不叠加”退出构建前置检查，保留该在用修复任务。完整命令与实际检查时间见当前副本的 `guest-build-gate.json`；这不是本基线启动的客机，不按旧PID进行清理。

下一步：现有消融实验归档退出后，先执行此独立构建入口；只有编译、链接及POSIX内容校验通过，才接独立空盘做真实 CAZA 放置与重挂载小测，再处理完整负载。OAZA-GC仍需补齐完整公式/伪代码，ColdMap仍需确认论文对应变体，不用简化猜测替代。

## 2026-11-06：独立构建与真实 ZNS 最小集成验证通过

本轮接续相关工作实现，修改仅在 `baselines/`。没有改动阶段四的机制、阈值或结果；阶段四18份已登记交付文件哈希均复核一致。当前独立源码为 `workspaces/caza-v5-20261106/`，包含阶段四已验收的观测退出头及设备Lease，头文件SHA256按阶段四最终实现清单核验；HeatProjection正文保持一致。

### 构建配置与失败定位

首轮完成8个相关单元及客户端的实际编译，链接因缺少SyncPoint和线程状态调试支持失败。补编真实支持代码后链接成功，但普通目录小测在 `DBImpl::ShouldPurge` 崩溃。失败归档保留，没有通过桩函数或删除检查掩盖错误。

诊断父镜像未安装GDB；该轮同样按失败归档退出。后续在独立目录编译诊断专用信号栈采集器，以同一程序重现崩溃，结合原库对象符号和反汇编定位构建配置问题：原库相关核心对象没有SyncPoint引用，同一成员在原对象中的偏移为 `0x14f0`，新对象为 `0x15f0`。仅凭 `librocksdb_debug.a` 文件名和当前 `DEBUG_LEVEL=1` 的Make dry-run不能确定已有库的实际布局。

按原库一致的 `NDEBUG` 配置重编后，已知崩溃函数的8个成员访问偏移完全相同，链接及POSIX测试通过。`build_guest.py` 现在先核验原库身份、原对象符号及这些偏移，再链接新对象；不再混入不匹配的调试布局。此偏移检查针对已知故障点，不能扩大为所有ABI均已形式化证明。原静态库构建前后始终核验为 `fe0123f63993b7a7a823bd114729bdf281696eb45e3d619034396bda8316c37b`。

新增 `diagnose_guest.py`、`device_guest.py`、`run_device_host.py`。设备准入要求成功的构建、已知偏移检查、POSIX原始日志及程序哈希；相关文件必须与已核验归档逐字节一致。新增3项准入回归，与已有隔离及源码筛选合计17项Python测试通过。链接成功而POSIX失败、解压日志被改写的情况均被拒绝。

### 功能载荷与实际结果

最终构建来源：`/home/data/xwz/rrzc-baselines/joint-related-caza-build-v5-20261106-results`。最终设备结果：`/home/data/xwz/rrzc-baselines/joint-related-caza-device-v3-20261106-results`。两者使用同一程序，SHA256为 `21c421c453ab01e735fa1e1579bb13970b2fa1002776b29b94e9a1bde5c74048`。

合成夹具为默认CF、两层LSM、2048个16字节定长Key、4096字节Value、无压缩、关闭WAL和自动Compaction。先分四批Flush，再覆盖前512个Key并Flush，手动压实产生真正的L1 SST，最后再次覆盖前512个Key并Flush。首512个Key的期望版本为2，其余为0。逐Key最新值、数据块校验和、关闭重开及文件系统重挂载均通过。

初版夹具的Compaction主要是层级移动；扩展到重叠输入后，RocksDB默认动态层会先实际写L6，再按 `target_level=1` 改标为L1，因而“实际L1写出”断言失败。该轮数据和映射检查虽通过，整轮仍保留失败结果。最终只将功能夹具固定为两层，验证真实L1写出，没有改CAZA算法或阶段四配置。

设备先核验FEMU控制器、8GiB、64个全空Zone及Open/Active=8/12。GC关闭，保留严格RR接口及主机RR设置；没有为本测试调整RR阈值。最终观测到6次实际空区分配和1次实际重叠区分配：SST13在L1写到Zone字节起点1610612736；随后SST14的L0写入命中相同Zone，日志记录 `overlap_ssts=1`。

原生 `GetZenFSSnapshot` 与RocksDB有效文件元数据独立核对：末端2个SST、12个extent、10663398字节；所有有效SST的extent长度之和与文件大小一致。目标日志中的SST/层级/Zone能在实际映射中找到，且该Zone确有下一层有效SST。重挂载前后的SST、层级及extent CSV完全一致。

两份组成记录均完整封存、无丢弃，离线事件账目闭合；原生映射核验次数分别为5和6，没有GC/RR复制字节。本轮是buffered写路径的最小集成验证，不能将“未发生复制”解释为已经验证GC/RR迁移，也不是收益数字。

### 收尾与剩余边界

本轮10项构建、诊断和设备功能任务（含失败轮）均完成归档哈希核验、正常退出及临时镜像回收；传输包已回收。最终真实进程复核无存活项目客机，没有留下常驻调试环境。逐轮归档身份、源码/程序身份、原生映射和未确认项见 `workspaces/caza-v5-20261106/本轮集成核验.json`、`最终资源核验.json`，原始失败现场仍可追溯。

CAZA尚未通过容量压力下的nearest/LIZA回退、直接I/O设备路径、GC/RR迁移及失败恢复、完整负载与性能验收。正式对比还需统一原机制与各基线的编译宏、观测配置和输入；不能把本次 `NDEBUG` 客户端与旧阶段四程序性能直接混比。`implementation_complete` 继续为false。OAZA-GC、ColdMap完整移植仍未完成。

## 2026-11-07：直接I/O与自然RR功能验证通过

当前独立源码为 `workspaces/caza-v6-20261107/`。本轮补充功能客户端、`rr_probe.h`、设备用例选择及准入核验；分配适配器增加候选不可用原因日志，原有排序、Acquire/Release、令牌及预留资源规则保持不变。修改均在 `baselines/`，不改原机制源码、第五阶段报告或已归档结果。第四阶段18份交付文件及独立副本2244个登记文件均按原清单复核一致。

### 同一程序的构建与直接I/O

最终构建为 `/home/data/xwz/rrzc-baselines/joint-related-caza-build-v7-20261107-results`，继续使用与原库一致的 `NDEBUG`，8个受影响单元及客户端编译、链接和已知ABI偏移核验通过。POSIX基本与直接I/O用例均完成最新值、校验和及重开检查。原库SHA256仍为 `fe0123f63993b7a7a823bd114729bdf281696eb45e3d619034396bda8316c37b`。

最终直接I/O和RR设备验证共用程序SHA256 `700df4a3ba031b2d14432607d4107303291b92595d21a9e6f6e43b727154ae6c`。两个结果目录分别为：

- `/home/data/xwz/rrzc-baselines/joint-related-caza-device-direct-v2-20261107-results`
- `/home/data/xwz/rrzc-baselines/joint-related-caza-device-rr-v2-20261107-results`

直接I/O用例显式启用 `use_direct_reads` 和 `use_direct_io_for_flush_and_compaction`，并由原生SST打开日志确认 `direct: 1`。沿用两层、2048个定长Key的功能夹具，真实L1压实写出、空区和下一层重叠分配、逐Key最新值及块校验和均通过；末端原生extent映射与重挂载后CSV完全一致。它验证直接I/O路径，不是吞吐或延迟实验。

### 自然读取触发RR与迁移后分配

RR用例先验证全部数据，再通过ZenFS直接读取真实L1 SST的同一4KiB页面，逐次检查页面字节不变；没有注入RC、指定victim或调整阈值。原C2/C3设备接口确认阈值仍为1024；本轮新增1个阈值事件，最高RC为1024。客户端每32次读取观察映射，至992次读取的检查点已观察到迁移；992不能解释成从零开始的阈值所需次数。

|项目|迁移前|迁移后|
|---|---|---|
|L1映射Zone字节起点|1610612736|402653184|
|源区主机/设备Reset代际|0 / 0|1 / 1|
|源区RC|68|0|
|目标区主机/设备代际|—|0 / 0|
|累计阈值事件|0|1|

实际RR迁移两个SST的12个extent，共10663398字节；离线组成账目逐笔确认12次复制均提交、2个映射事务结束，源区Reset后为空且有效引用为0，近期10/30秒窗口归零。运行与重挂载的组成记录均闭合，逐文件原生映射核验分别181和7次；存在忙文件跳过，不能将其称为跨层原子快照。原始复制、映射及Reset事件继续保存在归档中。

迁移后再覆盖首512个Key到版本3并Flush。CAZA实际检查的是新L1位置402653184、代际0；日志记录该候选 `reason=busy`，随后正常选空区939524096。该迁移目标属于公共预留池，在原有规则下保持独占；不能要求普通分配强行复用。最终最新值、校验和及重挂载前后映射检查通过，没有进入原协同池。

首轮RR的客户端已完成迁移和内容校验，但测试包装器错误地要求原有共置关系在迁移及后续Flush后仍成立，整轮按失败保留在 `joint-related-caza-device-rr-v1-20261107-results`。修正了测试断言，并增加候选拒绝原因的真实日志；没有通过修改预留资源所有权使测试通过。

### 准入、收尾与下一步

设备入口新增 `--case basic|direct|rr`。非基本用例要求归档中的 `probe.cc`、`rr_probe.h` 与当前协议逐字节一致，不能仅凭能力标志或日志文件名准入；直接I/O另核验相同构建的POSIX直接I/O成功日志。新增3项过期协议/能力标志回归，连同已有测试共20项Python测试通过。独立副本主机语法与4组策略/适配测试通过；最终程序另经过上述实际客机编译与执行。

本轮2次构建、4次设备任务（含失败轮）均完成归档哈希核验、正常退出、临时镜像及传输包回收，当前无存活项目客机。逐轮归档、程序与源码身份、原生映射、RR状态及实际资源核验见 `workspaces/caza-v6-20261107/本轮设备核验.json`。不覆盖旧v5记录。

已通过的是直接I/O和buffered数据库自然RR两个独立用例，尚未组合验证直接I/O与RR；GC关闭，容量压力下的nearest/LIZA回退、GC迁移、故障恢复、完整负载和性能仍未验收。起点并未采集完整逐物理块RC及待处理队列，不声称完整NAND检查点或与阶段四计时可比。`gc_rr_integration_verified` 和 `implementation_complete` 继续为false。

下一步先补CAZA容量压力下的真实回退及GC安全性验证，再进入完整对照负载。OAZA-GC仍需取得可逐公式/伪代码核对的完整材料，ColdMap仍需确认论文对应变体；二者完整移植尚未完成。

## 2026-11-07：Active名额回退与实际空间压力GC通过

本轮新增 `caza/pressure_probe.h`，扩展功能客户端、构建/设备入口及准入回归；没有修改CAZA排序、ZenFS生产执行路径或原机制。独立适配源码仍为 `workspaces/caza-v6-20261107/` 的2244份冻结文件，新增内容是其外部功能夹具。原库身份及 `NDEBUG` 构建、已知成员偏移核验继续通过；Python准入及隔离测试共22项通过。

### 真实nearest及LIZA回退

设备结果为 `/home/data/xwz/rrzc-baselines/joint-related-caza-device-pressure-v1-20261107-results`，构建为 `joint-related-caza-build-v9-20261107-results`，程序SHA256 `7790d5fec6a6fdb03c3dcd258cb9005ae4ddc89b54902771c060fd0850eea660`。

夹具使用6144个真实定长Key、4096字节Value、两层LSM和关闭自动压实。24个连续Key范围各Flush一次，再把首256个Key以相同值覆盖并Flush，保留重叠L0输入。该重叠输入用于避免当前 `Compaction::IsTrivialMove()` 将全部不重叠文件只改层级，确保真实L1写出。构建v8是检查这一条件前的夹具，编译及POSIX通过但未用作设备验收；v9补齐输入后重新构建，没有修改压实算法。

没有手工占锁、占用令牌、Finish、Reset或降低设备资源限制。正常Flush使普通Active名额用满：压实前原生快照仍有51个空Zone和10个部分Zone。实际观察到17次 `stage=nearest`：16次追加到1879048192，最后一次覆盖首段时追加到939524096。每次目标都与当时已提交的同层SST映射及最近Key范围核对，不能只凭目标日志判通过。

随后真实压实产生4个L1 SST，每个均通过CAZA的 `stage=liza` 进入原分配器，实际目标均为十六进制0x70000000（1879048192）。包装器同时核对该原分配日志和压实后的原生extent，未将“进入fallback”当成已分配成功。6144个Key全部最新值、块校验和及重挂载前后映射一致；两份组成账目闭合，原生映射核验分别42/8次，没有GC/RR复制。

这是Active资源名额压力下的回退验证，仍有大量物理空区；不能称为磁盘完全耗尽或NoSpace恢复验证。

### 真实SST失效与自然GC

GC结果为 `/home/data/xwz/rrzc-baselines/joint-related-caza-device-gc-v1-20261107-results`，构建为 `joint-related-caza-build-v10-20261107-results`，程序SHA256 `df10cb157bc4239b0b2f5fb6a8e21dd4b33aacc968be22f5024bdd888afa3956`。v10仅完善GC夹具的空间准备条件，2244份适配源码未变；仍重新完成独立编译、链接、已知ABI检查及POSIX基本/直接I/O/压力用例后才准入。

GC用例先在相同6144-Key数据库复现上述资源回退，再根据真实L1布局选择要更新的输入Key范围：85轮正常更新/Flush使共置Zone自然写满，局部压实使旧版本失效，同时保留同Zone内其他真实L1 SST。没有指定GC victim、迁移目的地或直接调用迁移、Finish、Reset。源区在加空间压力前仍有有效SST，且有效量低于Zone容量的四分之一。

另写54个普通非SST填充文件，共7184842752字节；逐文件路径、长度及可生成的字节模式见归档 `gc-fillers.csv`。这些文件仅形成空间压力，不作为RocksDB SST、冷度输入或收益数据。测试设置 `--enable_gc`，保留原GC的20%启动门槛、十秒检查节拍、无效比例资格及有效量Greedy。仅到20%空闲时原比例门槛为100%，不会使这个仍有效的源区合格，因此夹具继续填至约10.5%空闲；这是增加正常文件输入，不是降低GC门槛。

原生日志实际核验：空闲整数比例10%、原无效比例门槛70%，满Zone共54个。源Zone1879048192有效17140875字节，无效比例88%，原资格通过、候选范围扩大=0；原合格集合只有该区，Greedy实际选中1区。自然GC复制3个SST的19个extent，共17140875字节，3个映射事务均提交，重置1区；独立组成重建逐笔与此闭合。

源区主机及设备Reset代际均从0变为1，RC及近期10/30秒窗口归零。保留SST的新映射位于1073741824；C3阈值事件保持0→0，原生日志没有RR完成，因此该次迁移可明确归为GC。两份组成账目原生映射核验分别368/62次，运行记录有22次忙文件跳过，不冒称全盘原子快照。

GC后检查6144个Key的最新值（热范围版本85、其他为0）、块校验和，关闭重挂载后再次检查，原生映射CSV逐字节一致。填充文件未做全文件重读校验，不把它们计入数据库内容验收；也没有在GC后再做一轮新写入来单独覆盖目标选择。

### 收尾及剩余范围

本轮3个构建和2个设备任务均已归档校验、正常退出、回收临时镜像与传输包；没有留下客机。设备归档、程序与测试源码身份、原生映射、复制/提交账目、原始GC资格行及资源核验见 `workspaces/caza-v6-20261107/资源压力与GC核验.json`。旧 `本轮设备核验.json` 及第五阶段报告不覆盖。

本轮已补Active名额压力和自然GC单轮功能验证；物理空间完全耗尽后的回退/超时恢复、迁移失败恢复、直接I/O与GC/RR组合及完整负载/性能对照仍未验收。`implementation_complete` 继续为false。下一步先补迁移失败安全退出及明确的NoSpace边界，再考虑统一配置下的完整功能负载；OAZA-GC完整材料与ColdMap论文变体仍需核实，不能据此声称三个相关工作均已实现。

## 2026-11-07：复制错误、NoSpace恢复及论文材料核验

本轮仍只修改 `baselines/`：新增失败路径客户端、专用故障库、作者函数核验夹具，并扩展构建/设备准入。CAZA独立副本2244份适配源码、第四阶段18份登记交付及第五阶段21份既有文件均按原哈希复核一致。没有修改原机制、阈值、分配策略或既有结果。

### RR复制一次EIO后的正常重试

结果为 `/home/data/xwz/rrzc-baselines/joint-related-caza-device-fault-v1-20261107-results`，构建为 `joint-related-caza-build-v11-20261107-results`，程序SHA256 `f7a97304b91662c6380f96c71611ec8ab0b02034277fe25bcac32621ae62fe59`。8个相关单元及客户端真实编译、链接、原库身份与已知ABI偏移核验通过；POSIX基本、直接I/O、压力及故障库普通文件保护实测均通过。

`fault_io.cc` 只通过LD_PRELOAD装入故障用例客户端，明确arm后对 `/dev/nvme0n1` 数据区的一次pwrite返回EIO；不装入mkfs或原机制，不更改设备RR阈值。实际失败偏移402653184，错误为 `IO error: Input/output error`。失败时的原生extent CSV与RR前完全一致，源Zone1610612736代际未变。后续正常读取重试成功，没有手动指定victim或直接调用迁移。

最终实际复制12个extent、10663398字节，两个映射事务提交；事件中另有一个零提交结束事务，对应前次失败，不能把三次事务结束都算为成功迁移。源区代际0→1、RC归零，C3新增一个1024阈值事件。随后更新并Flush、2048个Key最新值/校验和及重挂载检查通过。运行与重挂载组成账目均闭合，原生映射核验196/7次，忙文件跳过78/2次。这只验证一次复制EIO，未覆盖崩溃、断电或元数据同步失败。

### 普通数据可分配空间耗尽与恢复

成功结果为 `/home/data/xwz/rrzc-baselines/joint-related-caza-device-nospace-v2-20261107-results`，构建为 `joint-related-caza-build-v12-20261107-results`，程序SHA256 `a5c7d13203b4b7797d769f699399cdf7c7ef90bef6719233708763e15151b920`。v12仍完成上述独立编译、ABI及三个POSIX用例，未复用旧程序假定新夹具已构建。

夹具使用6144个Key，GC关闭，普通非SST填充文件实际写入7516192768字节（7 GiB），直到原分配器返回NoSpace。随后正常数据库Flush同样返回NoSpace，保留原始错误和已提交SST映射；通过数据库快照核验最后成功Flush的数据，不把关闭WAL后尚未落盘的失败memtable算作持久化成功。继续使用原有有限重试和预留规则，没有强行填满迁移预留或元数据区，也没有缩短超时来规避真实路径。

正常删除两个本轮填充文件后，Zone939524096及1073741824均由代际1变为2；数据库重开核验最后已提交值，再完成版本1的新写入和Flush。全部6144个Key最新值、块校验和以及文件系统重挂载通过，末端与重挂载后的原生SST映射CSV一致。两份组成账目闭合，原生映射核验1078/64次、忙文件跳过46/2次，无有效迁移复制。填充文件保存生成方式与逐文件清单，未宣称逐字节重读了所有填充数据或保存了完整NAND状态。

前一次 `joint-related-caza-device-nospace-v1-20261107-results` 整轮失败并完整保留：预期的两次NoSpace及已提交数据检查通过，但只释放一个填充Zone后，重开产生的元数据先使用该区，后续SST仍无合法目标。修正仅是夹具释放两个自己的填充文件，分别为重开元数据和SST提供空间；没有取消内容隔离、抢占预留区或更改CAZA。旧失败归档的事件账目亦独立复核闭合，不能因此把该轮结果改为成功。

### ColdMap原函数及正文/SI获取

按用户授权同时查找正文与补充材料，采用 nature-downloader 的合法OA路线。两篇正文PDF的出版商访问均返回403，尚未取得完整正文；不能把访问失败写成论文非OA，也不能声称补充材料不存在。逐次结果见 `materials/paper-retrieval-20261107-v1/manifest.json`，当前缺项见 `materials/retrieval-and-source-status-20261107-v1.json`。已取得[作者ASPDAC 2024会议幻灯片](https://www.aspdac.com/aspdac2024/archive/pdf/5A-2.pdf)，SHA256 `7d3a4449321588aa20bc3cbcb9d67f48bac0745113785611676b5cfb454373cf`；它不是OAZA-GC期刊正文，不能代替算法1/2/3与完整公式。

另冻结[ColdMap ICS26作者仓库](https://github.com/sg20180546/ACM_ICS26_ZNS_COLDMAP)提交 `2c101302f3b4c29deecd11de86b0db65466f2fbc`。四个ZenFS核心文件与先前仓库逐字节相同，没有执行上游构建/发布脚本。新增 `coldmap/check_author_algorithms.py`，从固定哈希源码提取七个未修改函数，使用字段级元数据后端编译运行；真实RocksDB Slice头、严格警告及UBSan检查通过，函数逐项哈希保存在 `workspaces/initial-20261106/coldmap/author-methods-check-v1/result.json`。

核验确认：固定字节数据仅拆分extent，Zone平均冷度即可从0.5变为1/3，而该夹具按字节加权应为0.9；原代码并非README所述的大小加权。模拟一次压实传播的原函数实跑通过；若选中的文件在快照中缺失，原 `continue` 不减少步数或排除该文件，构造夹具观察到循环无法前进，由父进程1秒超时终止已知测试子进程。该结果针对不一致快照，不证明正常一致元数据必然遇到此问题。原函数夹具不是完整ColdMap移植或设备验收。

作者仓库含多种CBZC及分配模式，默认参数不能自动视为论文实验配置；模拟中临时层级大小更新与README所述生成新SST的关系也尚需正文核对。因此保留源代码行为和差异证据，未擅自选择变体、修改公式或将简化算法冠以ColdMap名称。

### 当前完成范围及收尾

23项Python隔离/准入测试通过，主机语法与四组独立适配测试通过；最终v12另经真实客机编译及执行。两次构建、三次设备任务（含失败轮）均完成归档哈希、提取证据逐文件一致性核验、正常关机、临时镜像及传输包回收。新的 `workspaces/caza-v6-20261107/失败路径与材料核验.json` 和同目录核验脚本记录实际来源及收尾，不覆盖旧核验凭据。本项目客机及监督已全部退出；发现的其他账号客机未操作。

**三个相关工作尚未全部完成。** CAZA已有上述独立功能证据，仍缺联合完整负载和性能验收；OAZA-GC缺完整算法图、公式与回收数量细节；ColdMap缺论文变体配置及代码差异的依据。已向用户请求两篇正文/SI的本地路径或可访问的图书馆入口，等待材料期间完成了全部不依赖这些缺项的本轮验证，不编造缺失公式或收益数字。

## 2026-11-09：正文核验与 OAZA-GC / ColdMap 独立接线

用户提供 `materials/oaza-gc.pdf`（25页，SHA256 `4875225654998cb28c740472015bcf1904600a57012b9094490730e97cc35cfd`）及 `materials/ColdMap_Compaction_Aware_Cost_Benefit_Zone_Cleaning_for_ZNS-Based_Key-Value_Stores.pdf`（14页，SHA256 `b1114dcabaf213b39ded8e711736e85148e31d5e7095817d2682b2f25a550b46`）。来源记录见 `materials/user-papers-20261109-v1.json`。已核对方法、公式、伪代码和实验配置；目录中未发现新增SI，不据此断言发行方不存在SI。旧403记录继续保留。

### 正文与作者代码的差异及复现约定

OAZA正文57:10的算法1/2区分顶层L0/L1、中间层及最底层：中间层引用下一层，底层引用上一层；按照重叠SST范围内的非dummy Key数量除以新文件Key数量计算OR，同层按OR差寻找能放完整文件的Zone。无重叠中间层文件写入新Zone并关联下一层。独立适配器新增实际 `VersionEdit` 层级提交接线，提交前在DB互斥区重新核对当前下一层范围及本次输出，冲突时失败而不安装重叠层文件。该分支仍需设备用例覆盖，不能以主机语法通过代替。

57:12公式1～4化简为 `数量=GC_TS×INVALID_RATIO÷AVRG_INVALID×TOT_ZONES`，原文GC_TS=0.15、AVRG_INVALID=0.5。固定参数实现用整数精确计算 `floor(3×invalid×zones/(10×allocated_capacity))`，避免浮点边界向下错一；整数舍入未由论文定义，向下取整是显式适配约定，允许结果为0，不暗中补成1。已分配Zone的无效量按WP消耗减实际有效量计算，排除尚未写入尾部；Finish牺牲的padding在当前快照中无法单独区分。总区数为ZenFS的io_zones数量，元数据Zone不混入。

57:13算法3确实在每个文件循环中覆盖 `cost_i`，没有定义多文件归并；同页文字把u同时称为有效比例又说按无效量计算。用户明确选择同时保留字面版与合理修正版，故 `OAZA_GC_VARIANT` 必须显式设置：

- `literal`：按伪代码，u为有效比例，成本为 `(1-u)×最后文件pri×label`；文件按编号升序，最后文件顺序依赖公开保留。
- `adapted`：工程修正版，成本为 `u×按Zone有效字节加权的pri×label`，不是作者确认的更正公式。
- `prose`：供核对文字定义，u按无效比例解释，仍保留最后文件覆盖。

pri在OR=0或OR>2×amp时为1，其余为OR/amp；严格保留 `>` 而非 `>=`。amp取真实RocksDB层级容量倍率。三者均升序选取动态数量，不引用本机制Credit/Score或单区预算。CAZA共享接口名称仅作为独立副本的数据传输设施，OAZA的实际分配日志使用OAZA标识。

ColdMap正文5.2明确要求生成并插入模拟输出SST，不能只更新临时层级总量；5.3式2要求按大小加权，而固定作者仓库按extent算术平均。新 `coldmap/policy.h` 实现前者：使用历史压实输出/输入比例预测大小，按最大文件大小拆分，再按大小比例划分固定字节Key范围；生成文件可参加下一轮模拟，原文件的传播冷度继续保留。112MB×0.73在精确算术中为81.76MB，拆为64MB+17.76MB；正文82MB/18MB是取整示意，不强行复制其舍入数字。

正文5.1将高L_score和高HF用于优先压实，却把直接归一化后的加权值称为“越大越冷”；作者代码补取反方向。因此 `COLDMAP_FACTOR_DIRECTION=printed|artifact-survival` 显式区分两种解释，尚不把某一方向宣布为最终论文更正。共同参数α=0.5，按最近3个ZC间隔成功压实数的平均向下取整计算模拟深度。DB桥接发布真实文件大小、层级、Key范围和epoch，另在原压实占用/完成路径记录状态及历史输出比例；不在DB锁内回调文件系统。

新模型仍有适配边界：单默认CF、定长Bytewise点Key、leveled kMinOverlappingRatio；OAZA拒绝空值/dummy及删除输入，不能把未经核验的Key计数当完整论文支持。公共迁移后端仅支持SST，遇到非SST/WAL或覆盖不齐则延后，不声称已移植ColdMap的WAL迁移及Universal分支。OAZA以15%进入GC，ColdMap当前保留公共20%GC启动及十秒检查，尚非论文中FAR/并发线程配置的原环境复现。Zone代际、源映射覆盖、公共目标余量及映射提交安全检查继续保留。

### 当前实测进展

主机正文算法测试包含OR计数、顶层/底层/零重叠分支、数量化简与整数边界、字面成本的文件顺序依赖、修正版字节加权及排序差异、ColdMap的K=3、extent拆分不改变字节平均、真实输出大小/范围示例、模拟生成文件参与后续步骤、Trivial Move、intra-L0及忙文件终止。首次测试有一个无法区分两种因子方向的0.5例子，修正夹具后UBSan通过；旧失败证据保存在 `workspaces/paper-models-20261107-v1/initial-failure.json`。

ColdMap `workspaces/coldmap-v2-20261107/` 首轮主机检查通过后完成独立客机编译，结果 `joint-related-coldmap-build-v1-20261107-results`。之后发现选择器Capture用的是观测设备编号，而绑定用设备指针，两者不一致；该轮仅POSIX测试，没有被用来宣称设备GC通过。修正后生成新 `workspaces/coldmap-v3-20261109/`，旧源码和归档不改写。新构建 `joint-related-coldmap-build-v2-20261109-results` 完成9个相关单元及客户端编译、链接、已知ABI及POSIX基本/直接I/O/压力检查。

真实设备结果 `joint-related-coldmap-device-gc-v1-20261109-results`：8GiB空盘、Open/Active=8/12，实际ColdMap候选Zone1879048192有效17140875字节、冷度1、分数6.830272841964。该次成功压实计数2、深度2，但没有满足继续模拟条件的压实，模拟步数为0，合格候选只有1个。实际迁移19个extent、17140875字节、3个映射提交，源区主机和设备代际0→1，RC/近期窗口归零，没有RR阈值事件。6144个Key最新值、校验和、重挂载及原生映射一致，运行/重挂载账目闭合，原生核验368/62次。该结果证明真实集成执行，不能证明多候选排序或模拟带来收益。

OAZA-GC `workspaces/oaza-gc-v1-20261109/` 的10个相关单元及客户端主机语法、真实客机编译、链接、POSIX基本/直接I/O/压力通过，构建 `joint-related-oaza-gc-build-v1-20261109-results`。`joint-related-oaza-gc-device-literal-v1-20261109-results` 完成两层压力放置、6144-Key内容/校验和及重挂载；未发生GC，不能当作成本或动态数量的设备验收。新增分配器夹具直接编译 `allocator.inc`，覆盖完整文件容纳、OR优先级、代际拒绝、fresh关联、Active失败归还及顶层复用。

后续独立GC夹具正在增加多个自然失效的满Zone，目的是让原数量公式产生正数后核验字面版与修正版；当前仅完成构建准备，不提前填写设备结果。两种方法完整负载、正常并发和性能对照尚未完成，`all_methods_complete` 继续为false。

### 同日后续：OAZA-GC两版真实回收与重挂载通过

最终OAZA源码为 `workspaces/oaza-gc-v2-20261109/`，10个相关单元、客户端和分配器UBSan夹具通过；真实程序来自 `/home/data/xwz/rrzc-baselines/joint-related-oaza-gc-build-v5-20261109-results`。五轮构建均保留各自结果，不用新源码或夹具哈希覆盖旧归档。

GC夹具用12组、每组6144个Key，共73728个Key，正常Flush和局部压实产生有真实保留数据的源Zone。完整SST容纳要求会留下小尾部，因此使用超过普通Active名额的不同数据组，由原分配器自然Finish旧尾部；未直接调用Finish/Reset，没有指定victim或迁移目的地。非SST填充文件只制造低空间压力，不作为寿命或OR输入。原数量公式保持向下取整，未添加“至少一区”。

成功结果分别为：

- `/home/data/xwz/rrzc-baselines/joint-related-oaza-gc-device-gc-literal-v4-20261109-results`
- `/home/data/xwz/rrzc-baselines/joint-related-oaza-gc-device-gc-adapted-v1-20261109-results`

两版第一帧均实际记录 invalid=1310362395、allocated=7918845952、io_zones=61，精确公式给出3区，均实际选择3区。字面版第一轮按成本选择1879048192、2013265920、2415919104；修正版选择1073741824、2147483648、2281701376。由原始候选成本独立排序所得集合，与该阶段实际提交复制的源区集合相符；不是只看“selected”文字判通过。

|版本与阶段|实际GC轮次/选区量|GC复制字节|RR复制字节|原生映射核验次数|
|---|---|---:|---:|---:|
|literal设备主阶段|2轮，3区+2区|129225145|0|1910|
|literal重挂载读取阶段|无GC|0|134194730|290|
|adapted设备主阶段|1轮，3区|76189592|0|1690|
|adapted重挂载读取阶段|1轮，2区|52971152|0|292|

字面版重挂载读取自然触发原1024阈值的RR：Zone2550136832复制134194730字节、重置1区。该量独立计入RR，不能混入GC。表中两版阶段内的GC次数和读取交错不同，不能用阶段间字节差或首轮差直接宣称稳定收益。

全部73728个Key最新值及块校验和、重挂载读取通过，前后文件身份/层级/总有效字节一致。literal物理CSV不同：正常后台GC/RR仍可在导出与重挂载之间移动文件。保留两份原始CSV，并以已提交复制链解释位置差异；本轮找到的链共解释207次extent位置变化。adapted该轮两份CSV恰好一致，但重挂载阶段仍有实际GC，不能把CSV相同当成没发生维护。两阶段原生GC/RR复制总量均逐字节等于独立trace复制账目。

`physical-mapping-audit.json` 的方法边界：CSV没有原子事件序号，核验寻找能闭合物理差异的已提交复制后缀，不宣称恢复了跨层原子导出时点。对缺失复制、未提交复制、文件身份或长度变化不会判通过。27项Python测试包含缺失/未提交复制必须拒绝的回归，另有正文公式及模拟UBSan、两种源码的原生语法与桥接/分配器测试通过。

三次前置设备失败继续原样保留：literal-v1没有自然写满预定源区；literal-v2在整批迁移未结束时导出，快照跳过忙文件；literal-v3的内容与重挂载读取已通过，但旧包装器要求物理CSV完全相同，遇到合法后续GC而失败。随后分别修正工作负载及验收边界，没有通过关闭GC/RR、更改算法、改写失败结果或删除原始证据来通过。

### ColdMap最新副本与资源边界

`workspaces/coldmap-v4-20261109/` 补齐“L0正在压实则不模拟冲突的base-level向下压实”，新增该冲突的有界退出测试，正文模型UBSan、9单元及客户端主机语法、桥接测试通过。该副本的真实客机构建曾被串行前置检查拒绝：当时另一任务 `joint-controlled-placement-wmig-build-v1-20261109` 正在占用8101，随后该任务进入自己的v2构建。未关闭或绕过其客机，未把先前v3副本的成功当成v4已编译。

ColdMap现有真实设备GC证据仍对应v3，最新v4的客机构建及设备复验待资源空闲；OAZA两版的中间层OR分配、零重叠实际层级提交仍缺设备覆盖，完整负载、WAL/Universal支持和正式性能对照未验收。因此保留 `implementation_complete=false`、`all_methods_complete=false`。正文现已取得，剩余工作不再归因为缺论文材料。

全部已执行相关工作客机均已归档正常退出，临时镜像及输入传输包已回收。逐文件归档身份、失败状态、原生GC/RR分账、实际源区和当前源码核验见 `workspaces/related-methods-20261109-v1/分阶段成本与归档核验.json`；已验收CAZA的2244份源码、阶段四18份交付和阶段五21份既有文件哈希不变。其他任务新增的wmig代码不属于本次修改，不宣称整个并发工作区没有变化。

### 同日继续：中间层提交、恢复后写入与真实多步模拟

上文v2/v4及资源占用状态是当时记录。当前OAZA隔离源码为 `workspaces/oaza-gc-v7-20261109/`，构建为 `joint-related-oaza-gc-build-v9-20261109-results`；ColdMap隔离源码为 `workspaces/coldmap-v5-20261109/`，构建为 `joint-related-coldmap-build-v5-20261109-results`。两者分别编译11/9个受影响原生单元及独立客户端，并通过原库哈希、已知ABI、POSIX基本/直接I/O/压力验证。原库、CAZA冻结树和原机制未原地替换。

OAZA补齐以下恢复边界，均只在独立副本启用：

- 在SST可选属性 `rrzc.oaza.nonempty.keys.v1` 持久化非空点值计数，重开数据库时读取表属性恢复；缺失或与表项计数不一致则拒绝，不能把未知值当0。增加了恢复读表属性的I/O，未声称恢复时间不变。
- 完整SST放置按缓冲分块、稀疏头与块对齐计算物理上界；超过单Zone大小明确拒绝，不能用逻辑文件大小保证物理容纳。
- 零重叠关联后同步持久化ZoneFile寿命标签；重挂载原生Zone标签未设置时，从实际有效文件提示恢复，混合提示取最大值是本适配约定。原提示仅有有限档位，深层LSM不能逐层唯一编码。

`joint-related-oaza-gc-device-middle-v2-20261109-results` 已通过：真实四层DB中，首个L2输出零重叠后提交到L3，后续L2输出OR=4并进入同一目标Zone；两层文件同时存在且内容正确。重挂载先核对原映射，再写入新版本并压实到L2，恢复后的计数仍可支持OR=4分配，2048个Key最新值及校验和通过。最终映射变化包含这次合法新压实；恢复核验使用写入前的 `verify-before-write.csv`，不要求新文件与原文件编号相同。

ColdMap发现公开 `CompactFiles` 与普通后台压实完成通知走不同成功路径；当前副本分别计数，避免手工压实没有进入自适应窗口。`joint-related-coldmap-device-model-v2-20261109-results` 和 `joint-related-coldmap-device-model-printed-v1-20261109-results` 分别验证两种因子方向：3次真实压实、深度3、模拟3步、生成2个SST，其中1个生成文件成为后续pivot。预测步骤为L1→L2重写、旧L2→L3 trivial move、生成L2→L3 trivial move；没有将模拟步骤冒充真实压实。2048个Key、最新值、校验和、重挂载及两阶段事件账目通过。

前一轮 `joint-related-coldmap-device-model-v1-20261109-results` 的两步夹具失败继续保留：真实压实计数通过，但未满足“生成文件在后续步骤成为pivot”的断言。旧L2文件可能先被选中；新夹具增加一次实际压实并在断言前保存输入和各模拟步骤，深度仍由真实计数计算，没有固定修改算法的N。

正文模型及物理对齐UBSan核验见 `workspaces/paper-models-20261109-v3/`。本次核验命令首次漏掉common头文件目录，编译失败日志原样保留，补齐include后通过；没有因此修改模型源码。独立归档审计另复算ColdMap候选分数、最大分数选择及实际提交源区，并检查真实快照的生成文件/后续pivot证据。

最新OAZA两版GC复验均完成归档和退出：`joint-related-oaza-gc-device-gc-literal-v5-20261109-results`、`joint-related-oaza-gc-device-gc-adapted-v2-20261109-results`。两版初始invalid=1310360883、allocated=7918845952、io_zones=61，均有6个合格候选，公式给出3区；第一轮选择仍分别为前述两组不同源区。可选SST属性改变了文件物理大小，本轮字节数不能与旧v2源码结果混为同一程序身份。

|最新版本与阶段|实际GC选区量|GC复制字节|RR复制字节|原生映射核验次数|
|---|---|---:|---:|---:|
|literal主阶段|3区+2区|129225745|0|1867|
|literal重挂载读取|无GC|0|134195300|290|
|adapted主阶段|3区+2区|128093711|0|1913|
|adapted重挂载读取|无GC|0|133993134|290|

73728个Key、校验和、原生映射、复制—提交—Reset账目均通过；两版的物理CSV差异均由已提交复制链闭合。两版候选label在主阶段均为3。重挂载阶段本轮均未触发GC，因此当前真实证据覆盖恢复后继续写入及RR，不将其扩大为“恢复后GC成本标签已经实测”。后台维护交错及单轮功能夹具不支持在两版中定优劣，继续按用户要求并存。

ColdMap当前v5副本也通过 `joint-related-coldmap-device-gc-v3-20261109-results`：实际GC迁移19个extent、17140875字节，重置1区，运行/重挂载原生映射核验368/62次，RC清零、6144个Key与校验和通过，无RR。此GC夹具仍只有1个候选、深度2且模拟0步；三步生成文件证据来自单独四层模型用例，不能把两种用例拼成“多步模拟已经在本轮GC中驱动多候选选择”。

OAZA物理容纳上界的直接I/O分支另经 `joint-related-oaza-gc-device-direct-v1-20261109-results` 验证：使用同一v9程序，直接写入、压实输出、最新值、校验和及重挂载通过；该用例未开启GC，未与RR组合验收。

本轮30项Python测试通过，其中新增审计反例会拒绝错误ColdMap分数、低分受害区或实际复制源不符。最终不可变归档、当前冻结源码与资源核验见 `workspaces/related-methods-20261109-v2/分阶段成本与归档核验.json`；不改写v1凭据。仍待完成的验收是统一逻辑输入/起点的完整负载对照、ColdMap多候选且实际模拟非零的联合GC用例，以及OAZA重挂载后GC成本用例。WAL/Universal等超出当前适配范围，不能由这些点值/leveled功能测试推定支持。故 `all_methods_complete=false` 和 `implementation_complete=false` 继续保留，不将“编译和功能通过”写成“全部论文实验复现完成”。

### 后续联合验收（2026年11月9日，进行中）

本轮只扩展独立功能客户端和验收工具，CAZA v6、OAZA v7、ColdMap v5三份冻结策略树不改。没有把本轮夹具当正式收益实验。35项Python测试及三个生成客户端的主机语法检查通过。

ColdMap `joint-related-coldmap-build-v6-20261109-results` 和 `joint-related-coldmap-device-joint-v1-20261109-results` 已通过并退出。3组正常Flush/Compaction产生3个满且部分失效的SST Zone；同一GC帧真实成功压实6次、深度6、模拟6步、候选3个。候选有效量25592078、25592078、17140875字节，冷度均为1，分数分别4.244502927820、4.244502927820、6.830272841964；实际提交源为最高分的Zone1879048192，迁移17140875字节并Reset1区。18432个Key最新值、校验和、重挂载、GC/RR分账和物理复制链通过。6步都是trivial move，未生成新SST；这覆盖多候选/非零模拟/实际选区的共同执行，不证明预测改变了victim或降低了迁移成本。复杂生成SST仍对应前述独立四层模型用例。

CAZA新增同一负载中GC后再正常读取达到1024阈值的RR客户端，只选择读取哪个当前L1 SST，不指定迁移源或目的地。`joint-related-caza-build-joint-v2-20261109-results` 编译、链接、已知ABI和POSIX验证已通过。设备联合用例仍待结果，不能提前记为完成。

OAZA新增`recovery`用例：准备阶段只生成并持久化真实失效状态，重挂载后读取SST属性恢复非空Key数量，保存原生Zone未设置标签与有效文件提示，再施加原空间压力触发GC。主机审计用恢复后的Key范围/计数、原生extent字节和文件标签重算第一轮候选成本，字面版与修正版分开验收。重挂载主机观测代际重新起算，因此等待Reset使用本进程的代际基线，不将不同进程的计数直接比较。

串行监督入口为 `common/run_joint_validation_host.py`，当前队列状态见 `workspaces/joint-validation-20261109-v1/queue-v3/queue.json`；每项经过原入口准入、归档和镜像回收，再等待其监督退出及归档后的trace审计完成。队列等待其他整组实验监督结束，也等待结果整理锁，不利用两臂之间暂时无客机的间隙插入任务。认证只通过内存和匿名管道，未写入命令参数、环境或文件。

前置失败保留：首次CAZA准备遇到旧清单没有method字段，未启动客机，见原输出`未启动失败.json`及queue日志；限定旧冻结树接入后使用v2构建标签重试成功。第二次队列在结果整理锁上被拒绝，未创建设备输出；后续队列复用已核验构建并等待整理锁，未覆盖旧日志或重建成功任务。其余设备结果仍以实际归档为准。

### 联合验收完成（2026年11月9日15:21核验）

上节进行中状态已由真实结果补齐。`workspaces/joint-validation-20261109-v1/queue-v4/queue.json` 的6项任务全部完成；构建和已成功设备任务复用通过身份及归档检查的结果，没有重复执行。最新构建为CAZA `build-joint-v2`、OAZA `build-v10`、ColdMap `build-v6`，均为`joint-related-...-20261109-results`目录，原库哈希未改变。

|功能用例|准备阶段GC字节|设备/恢复阶段GC字节|RR字节|结果|
|---|---:|---:|---:|---|
|CAZA同负载GC→自然RR，18432 Key|不另分准备进程|17140875|25592078|各Reset1区，内容、校验和、重挂载及复制链通过|
|OAZA literal恢复后GC，73728 Key|0|129225745|0|两轮3+2区；恢复标签、Key计数和首帧全部6个候选成本复算通过|
|OAZA adapted恢复后GC，73728 Key|0|128093711|0|两轮3+2区；同样通过恢复输入与首帧成本复算|
|ColdMap artifact-survival联合GC，18432 Key|不另分准备进程|17140875|0|3候选、深度6、模拟6步，最高分源区实际复制及Reset|
|ColdMap printed联合GC，18432 Key|不另分准备进程|17140875|0|同样通过多候选/模拟/实际GC共同执行|

对应设备标签依次为 `joint-related-caza-device-joint-v1-20261109`、`joint-related-oaza-gc-device-recovery-literal-v2-20261109`、`joint-related-oaza-gc-device-recovery-adapted-v1-20261109`、`joint-related-coldmap-device-joint-v1-20261109`、`joint-related-coldmap-device-joint-printed-v1-20261109`。CAZA的RR源计数由509自然读至1024，实际发出544次读，源主机/设备代际均1→2且RC归零，没有预设迁移目标。OAZA两版首轮实际源区分别仍为1879048192/2013265920/2415919104与1073741824/2147483648/2281701376；这是功能夹具的单轮选择差异，不能据总字节差定优劣。ColdMap两版此次冷度均为1，6步全是trivial move，尚未证明预测改变选区或带来收益。

首次literal恢复用例`recovery-literal-v1`的客机内容/成本/账目通过，但旧物理映射审计失败，队列按要求停止并归档退出。定位发现CSV在第一轮GC结束后导出，程序结束前第二轮继续执行：前87次已提交复制精确还原CSV，之后58次共50836246字节属于更晚维护。旧审计强制重放至trace末尾，错误地要求CSV包含后续维护。现核验连续已提交复制区间，明确报告区间之后的复制次数和字节，完整trace账目仍独立全部闭合；不把区间结束当原子导出时间。新增缺失复制、未提交复制拒绝及后续维护边界测试，36项Python测试通过。v1失败凭据不改写，新标签v2重新运行后通过；离线复核另存`初次恢复归档时间范围复核.json`。adapted导出后同样有58次后续复制、51903771字节，均保留在完整维护账中。

同时修正设备监督状态中的GC开关记录，使其与`gc/joint/recovery`实际mkfs参数一致。较早CAZA/ColdMap联合用例状态曾继承公共模板的`GC启用=false`；其原始`mkfs.command.json`实际启用GC，`device_complete.json`及真实ZC复制也有独立证据。旧归档保持原样，不通过改写状态文件消除这项记录差异。

最终证据见 [最终归档核验.json](workspaces/joint-validation-20261109-v1/最终归档核验.json)：核对41条历史记录（含未启动失败），保留失败业务和首次后处理失败状态；三份冻结树的2244/2247/2247个源码文件、阶段四18份及阶段五21份交付哈希不变。归档、原生GC/RR分账、实际源区、完整组成trace和CSV复制区间均通过。本轮相关客机、子监督及队列已退出，临时镜像/传输包回收，最终核验未发现其他活动客机。没有修改原机制、阶段六准入规则、任何冻结算法树或旧归档，没有Git提交。

### 下一步：统一负载对照的实施边界

现在可以进入共同负载接线；`functional_joint_acceptance_verified=true`，但`all_methods_complete=false`仍保留。下一步先使同一已核验客户端能链接四类独立程序：原机制、CAZA、OAZA-GC（literal/adapted分列）、ColdMap（printed/artifact-survival分列）。固定共同RocksDB版本、逻辑操作与逐请求顺序、设备容量/Open/Active、原RR阈值及观测开关，分别记录源码和程序哈希。各论文的GC触发及选区规则单独记录，不能为了数字一致悄悄统一成另一算法。

先运行同一输入的有限前缀，逐条核对请求、可采集的起点Zone/WP/有效extent/物理身份/RC/近期窗口、维护复制—映射—Reset及结束残留；未直接采集的状态单列为未确认。通过后再串行执行完整输入，各自归档退出，不增加Greedy重复组作为门槛。不把本页不同Key数的功能用例当同对性能实验，也不把普通qcow2称作NAND检查点。

当前未实现完整负载统一入口、正式收益对照及统计重复；尚未支持的WAL/Universal、空值/删除、其他CF等保持原限制。ColdMap复杂生成SST与多候选实际GC的联合收益仍缺实测，两种因子方向继续并存。后续需要核验完整运行后的迁移总量、收尾残留和吞吐/尾延迟，才可讨论收益；本次没有启动该性能实验。

### 统一客户端与首窗口验收（2026年11月9日，进行中）

新增`comparison_source.py`、`run_comparison_host.py`、`comparison_guest.py`、`analyze_comparison.py`和`verify_comparison_host.py`，均位于`common/`。复用已核验的`校准-db_bench_tool.cc`及应用队列/请求生成头，在共同副本增加1～12窗口的显式前缀参数、装载输入CRC32C、全库逻辑摘要和显式ZenFS日志；不改三份冻结策略树或原客户端。40项入口测试后，新增前缀完成数量与“真实Flush之前不得确认写入”的回归；修正测试代码一次变量作用域错误后，41项通过。原12窗口审计器保持原样，独立适配层仅替换本轮预注册请求数，保留其逐笔时间、队列、批次及Flush确认检查。

三份共同客户端构建`joint-related-compare-build-v2-20261109-results/{caza,oaza_gc,coldmap}`全部成功，原库未覆盖、ABI检查及POSIX装载/摘要/校验和通过，客户端6份源码哈希一致。G2使用同一CAZA构建程序但不设置`ZENFS_RELATED_WORK`，显式设`ZENFS_RRZC_PLACEMENT_MODE=G2`，因此走既有原机制路径；其余臂明确启用各自方法，OAZA和ColdMap的歧义变体仍分列。此处复用相同库底座，不把它描述成原机制旧二进制完全未变。

本轮共同起点是各臂独立的全空8GiB FEMU设备：64个Zone、Open/Active=8/12、原1024严格RR阈值，逐Zone WP及8192块物理身份/代际/RC/近期窗口直接查询；设备会话和采样时钟保留但不要求数值相同。随后各自正常装载1048576个4KiB非空值，再执行原逻辑负载第一个窗口：65536读、122880写，写批2048、应用队列4096，独立Flush/Compaction及原后台维护，不设置批次GC门控。装载后的物理布局由各方法决定，保留各阶段SST/extent与设备快照；没有声称业务开始时物理布局或RC完全一致，也没有复用qcow2恢复内存NAND。

进展与原始错误保留在`joint-related-compare-prefix-v2-20261109-results`及其`resume-v1/`。v1设备用例因NDEBUG的URI工厂不创建文本日志而在装载后停止；另发现调度器恢复不能写入带方括号的显示字符串。修正为客户端显式创建Logger和恢复选中的调度器名称，使用v2构建及设备标签重新运行，v1归档不改。v2的G2设备阶段成功，但旧完成审计硬编码12窗口而拒绝1窗口；独立数量适配后原始188416条请求及完整账目通过，复用该成功G2归档继续其他臂，没有追加Greedy重复。续跑前状态和旧监督日志分别保留。

G2首窗口本身无GC/RR，重开验证阶段却因全库读取触发4429023993字节RR；该量归入`reopen`，不混入业务窗口。`load/run/verify/reopen`四段均独立保存完整trace及原生日志，不能只选业务片段宣称总维护减少。CRC32C用于共同输入和逻辑内容一致性核验，不是逐字节或每Key版本证明；当前生成器存在重复值，最终摘要不能单独证明每次覆盖的新版本。

六臂目前仍在串行验收，完整12窗口及性能结论未完成。全部完成后需由`verify_comparison_host.py`复查不可变归档、相同逻辑请求、实际缓存/Direct I/O、源身份及退出状态，才能将首窗口准入标记为通过。

### 六臂首窗口最终通过，完整输入已启动

`workspaces/comparison-20261109-v1/六臂首窗口最终核验.json`于2026年11月9日16:38完成独立复核。六臂各188416条请求均逐条对应原计划，逻辑请求摘要均为`14efc323daa72aff91521ee562abc30b9d8dd2b69e006b430012b7b9bd965589`；装载输入和结束逻辑CRC32C均为2534095423，重开后相同。全部24段组成trace闭合，实际缓存、Direct I/O和单文件打开线程配置通过；冻结CAZA/OAZA/ColdMap的2244/2247/2247份源码、阶段四18份及阶段五21份原交付哈希不变。六臂客机和监督均已退出，临时镜像与传输包回收。首次总核验的Python tuple与JSON array类型比较失败经确认仅为表示差异后修正，原值未改。

首窗口的业务GC均为0；OAZA两版业务RR各127752608字节，其余0。验证扫描额外引发的RR分别为G2 4429023993、CAZA与ColdMap两版各4294810943、OAZA两版各3623760998字节，不把这些数从总账删除。装载后的512份SST层级分布均相同，但G2/CAZA/ColdMap占40个SST Zone、OAZA占33个，因此只确认全空设备起点语义相同，不称装载后物理状态一致。该短前缀尚无GC选择证据，不能用于比较GC评分收益。

完整六臂已启动在`/home/data/xwz/rrzc-baselines/joint-related-compare-full-v2-20261109-results`，监督PID2736012（仅为本次启动记录，后续须核验真实存活），独立监督日志及`comparison-state.json`记录当前臂和其唯一客机。启动前再次读取全部前缀不可变归档并冻结到`prefix-admission.json`，核对与完整轮使用相同构建。每臂12窗口、读786432、写1474560，共2260992条请求；并行客机上限1，无额外Greedy重复。OAZA保持15%GC启动，其余20%，不把整套方法差异都解释为评分函数差异。

新增`summarize_comparison.py`只消费最终核验通过的归档，分别输出业务GC/RR、四阶段维护、验证读额外维护、应用完成延迟与阶段末端设备空间。空间按真实Zone WP和capacity计算，Finish/写入占用不能解释为有效数据。相关回归共42项通过，包含已Finish padding与逻辑有效量的区别。完整输入结果未返回前，`shared_workload_full_verified=false`，不提前填写收益表或更新论文结果。

原机制臂的具体边界：本轮`g2`显式使用G2放置及公共`SIMPLE_MODE=valid`有效量Greedy GC，`RRZC_CAL_GREEDY_EXECUTE=1`；没有启用完整Credit/Score受害区模型，也未包含另一任务开发的H普通目标回退。该臂用于三项相关工作的共同负载集成与G2放置参照，不能替代最终联合机制全功能版本的论文性能对照。

完整G2已完成2260992条请求、两次全库摘要/校验和及全部维护账目：业务GC 700176940、RR 696322261字节；verify阶段GC 134213033、RR 99862878字节；reopen阶段RR 4262507491字节。尚未对其余完整臂填写数值。G2归档退出后，下一臂准入因200GiB目录预算被拒绝（当时已占206318100480字节，加8GiB预留超限），没有启动CAZA，也没有降低保护线。

按既有保存范围完成旧数据无损整理：`compact_closed_requests.py`先核对五组已完成旧实验的原归档哈希及28份独立inode请求记录/全部别名，再gzip压缩、核对解压SHA256、写入可恢复清单后回收未压缩副本，共释放2531004416字节。原tar归档和源/程序身份不改；各旧目录新增`共同负载准入-旧请求无损压缩-20261109.json`，需要原路径时按该清单从gzip或原tar恢复并核对SHA256。预核验最初将独立采证副本误当硬链接而停止，未更改数据；改为逐副本匹配实际归档成员后通过。另将已归档退出的旧失败`joint-controlled-integrated-pool-v1-20261104`的FTL日志无损压缩，释放339529728字节，原失败状态和归档不变。证据见`旧请求压缩预核验-v2.json`、`旧请求无损压缩核验.json`、`旧失败FTL无损压缩核验.json`。没有删除唯一请求或维护证据、镜像或源码。相关测试增至44项通过。

后续续跑重新核对前缀归档及共同构建，复用成功G2，不加跑Greedy；旧监督日志和预算拒绝状态另存`resume-v1/`，新监督继续只跑剩余五臂。完整验收标记仍为false，待全部结果及最终核验。

续跑监督PID为2745062，自动最终核验监督PID为2745721（均为启动记录，须结合`/proc`及命令身份判断真实存活）。当前状态和后续结果入口为 [完整轮自动收尾/completion-state.json](workspaces/comparison-20261109-v1/完整轮自动收尾/completion-state.json)。自动监督不接触客机，不持有认证；等待实验控制器全部完成并退出后，核对被冻结的核验代码哈希，执行六臂不可变归档/逐请求/缓存/严格RC/原映射账目核验，再生成`完整六臂最终核验.json`和`完整六臂结果/`。只有这些步骤全部通过才更新`shared_workload_full_verified`；任何控制器失败、源码变化或核验失败都保留错误并停止发布。它不将`all_methods_complete`改成true，也不声明统计显著性。最新45项Python测试通过；前一轮自动收尾模块导入BASE路径错误已在启动前修正，原失败日志保留。

### 完整轮 OAZA 失败已归档，独立续跑 ColdMap

上述完整轮并未六臂全部通过。G2 与 CAZA 完成全部 2260992 条请求及四阶段审计；OAZA literal 在第 404 个同步写批后因 Zone 分配不足异常退出，adapted 则完成 720 批全部请求后在 verify 的 DB::Open 失败。两次均已完整归档、正常关闭客机并回收临时镜像。失败诊断、原始行号和界限见 [OAZA失败诊断与续跑边界.md](workspaces/comparison-20261109-v1/OAZA失败诊断与续跑边界.md)，不能把失败前部分维护汇总写成整臂通过。

本轮只修改宿主续跑/失败归档工具：显式 `--continue-after-failed-arm` 在逐文件核对失败原 tar、非零退出码及客机/镜像收尾后继续其他独立臂；失败保留在 failed_arms，不进入 completed，最终为 complete_with_failures。部署/装载失败、归档不完整及审计不通过仍停止。原冻结策略树、OAZA literal/adapted 成本公式、公共迁移执行及设备配置不改，客机程序无需重编。48 项 Python 测试通过，前一版宿主不兼容 Python 3.10 的 file_digest 调用及仅识别 run 失败的问题分别修正，原失败日志/状态保留在各 resume 目录。

修正版仅装载/业务阶段的复核确认逐条请求相同、完整组成 trace 闭合、业务 GC 3069570483 与 RR 1060784290 字节；后续内容摘要及 reopen 缺失，不构成完整验收。重开期间 GC 元数据尚未发布的启动依赖仍需验证，不把共享旧报错 `ColdMap requires exactly one DB` 解释为已证实多个 DB。

续跑前再次保持 200 GiB 目录上限及 8 GiB 启动预留，无活动客机期间将另外五组已归档旧实验的请求冗余副本无损压缩，解压哈希及原 tar 核验通过，共回收 1082777600 字节。记录为 `ColdMap续跑空间预核验.json`、`ColdMap续跑空间补充预核验.json`、`ColdMap续跑旧请求无损压缩核验.json`；旧目录各有恢复清单。未删除唯一证据或原归档，未改 paper 或原机制代码。原完整验收标记继续为 false。

`resume-v3` 已重新核验并复用 G2/CAZA 成功结果，两份 OAZA 失败原 tar 与解压文件全部一致后分别登记；不会重跑这四臂。2026 年 11 月 9 日北京时间 20:17 启动 ColdMap printed，完成归档和退出后才启动 artifact-survival。监督 PID 2773100、只读收尾监督 PID 2773105、printed 客机 PID 2773524，均仅为本次启动身份，后续以实际存活及命令核验为准。当前结果路径及镜像/QMP/8101 控制端口记录在各臂状态；无额外保留客机。收尾进度入口为 [完整轮续跑收尾-v3/completion-state.json](workspaces/comparison-20261109-v1/完整轮续跑收尾-v3/completion-state.json)，存在失败臂时不生成六臂通过或性能汇总。`失败诊断后冻结源核验.json` 再次确认三树 2244/2247/2247 份源码及阶段四 18 份、阶段五 21 份原交付不变。

### 恢复启动依赖的隔离修复验证

完整六臂尝试现已结束：ColdMap 两版与 OAZA adapted 均在业务进程退出码 0 后，verify 的读写 DB::Open 报 NoSpace、退出码 1；未取得最终内容摘要。两版 ColdMap 也都在该等待期间记录 `ColdMap requires exactly one DB`，与旧桥在 live_ 为空时的分支一致，不能解释为实际多个 DB。状态为 complete_with_failures；六臂全通过及论文性能结果仍未成立，旧客机/监督退出，临时镜像回收。

原生日志进一步定位到 MANIFEST 已取得类别为 Other 的部分 Zone，但 MANIFEST 保持该 Zone 独占，紧接的 CURRENT 临时 `.dbtmp` 无普通目标而进入两次 60 秒分配等待。GC 每 10 秒尝试，却尚无已发布的恢复 LSM 快照。公共内容隔离不允许把元数据写入 SST 区；既有 NoSpace 用例也显式释放两个普通区才能重开，说明这一资源需要早已存在。该证据支持修复启动依赖，尚不能把全部失败都归于同一单因。

新生成 `oaza-gc-v8-20261109` 与 `coldmap-v6-20261109`，保留 v7/v5 原样。每份仅修改 `db_impl_open.cc` 与 `rrzc_caza_bridge.h`：Recover 成功之后、CreateWAL/LogAndApplyForRecovery 写入之前，从已恢复 Version 发布相同字段及相同选区配置；OAZA 先固定 Version 引用、释放 DB 锁读取真实 SST 可选属性，再校验 Key 数并发布，替代原打开末尾的恢复时点。缺属性/错误计数仍拒绝，不填猜测值；新库跳过此恢复钩子。错误消息区分“尚未发布”与“多个 DB”。评分、动态回收数量、GC/RR 阈值、放置和普通分配器均未改。

两份新树的全部受影响原生单元主机语法、桥/分配器 UBSan 测试通过；Python 50 项通过，覆盖发布早于所有恢复写入、Key 数先校验、原策略字节不变及重复覆盖拒绝。客机串行队列位于 [recovery-bootstrap-20261109-v1/queue.json](workspaces/recovery-bootstrap-20261109-v1/queue.json)，监督 PID 2793218 为本次启动记录。任务为 ColdMap 独立构建及 joint GC/重挂载、OAZA 独立构建及 adapted recovery 用例。该队列是功能回归，不等于重现完整末端空间耗尽，也不替代后续同条件完整轮；当前不修改共同负载旧源码映射、不重写旧归档或 paper。

以上四项客机任务现均完成并退出。`recovery-bootstrap-20261109-v1/功能回归最终归档核验.json` 逐文件比对四份原 tar（54/36/56/38 个文件），确认冷映射及真实维护账目通过；两份重开原生 RocksDB 日志均出现 `Related recovery snapshot published before open writes`。ColdMap 重开组成账目闭合、无新增复制，OAZA 重开实际复制 128093711 字节且账目闭合。这些仍只证明功能回归及恢复发布确实执行，不能代替低空间完整输入验证。

新增 `run_bootstrap_full_host.py` 仅复验原失败 OAZA adapted 的 12 窗口、2260992 条请求：先用 v8 隔离树构建相同客户端，再运行 load/run/verify/reopen，逐条请求、完整内容摘要及维护账目沿用原检查。原客户端六份源码字节不改，参考旧 G2 仅核对逻辑输入与初始空设备语义，不合并为新六臂性能对照，不重跑 Greedy 或 CAZA。入口支持显式新 overlay，但旧 METHODS 默认冻结映射不变；对照入口只增加可选参考结果路径，原默认行为不改。52 项 Python 测试通过，其中功能队列失败时禁止进入完整复验。

原目录预算余量不足，继续在无客机及维护锁保护下无损整理已归档旧请求：八组原件/全部别名核验并压缩释放 1288024064 字节，另三组早期仅剩采证副本，最初因缺本地原件而只读停止；随后按确定的原 tar 成员逐字匹配，通过后压缩再释放 404275200 字节。原 tar 均保留，新增逐组恢复清单；缺成员或 SHA 不符仍拒绝，新增反例覆盖。证据见 `完整输入复验空间预核验-v2.json`、`完整输入复验旧请求无损压缩核验.json`、`采证副本空间预核验.json`、`采证副本无损压缩核验.json`。启动时目录占用 205758791680 字节，未降低 200 GiB 上限或 8 GiB 启动预留。

本次独立复验路径为 `/home/data/xwz/rrzc-baselines/joint-related-compare-bootstrap-oaza-v1-20261109-results`，监督 PID 2797714 为启动记录；`comparison-state.json` 记录当前构建/设备阶段及成功或失败收尾。该入口仍沿用图像生命周期、结果保存策略和串行唯一客机保护；当前不提前声称低空间重开已通过。

### 完整复验发现第二层进度问题，保留失败并修正批量执行顺序

v1 完整复验业务 720 批结束后，verify 仍报 NoSpace。本轮 GC 已取得真实 OAZA 帧，说明提前发布确实解除元数据缺失的启动依赖；但首次 GC 选择 4 区、实际复制 130101129 字节却重置 0 区，接着候选全部不满足剩余目标容量。公共 `MigrateExtents` 原按全局文件名分组，可能交错复制多个源区；逐源容量检查没有为整批预订容量，在清空任一区前便耗尽唯一目标。不能将这次失败改写为“完整修复通过”。

DB::Open 报错后该校验进程停留在关闭流程，线程现场见失败 tar 中 `stalled-open.json`：主线程与后台线程等待、GC 线程仍在运行，客机无 gdb，未编造用户态栈。先确认 PID 1292 的程序完整路径、verify 阶段、NoSpace 日志及超过 60 秒未正常退出，再向该进程发送 SIGTERM；没有 SIGKILL，也未终止其他任务。随后原监督自动归档并正常关闭客机。`首次完整修复复验失败归档核验.json` 逐字匹配原 tar，实际 verify 退出码为 -15。该信号处理不保证未写出的内存 trace 完整，明确不发布完整维护账目或 NAND 快照。自动审批第一次超时未执行，唯一一次重试获准后完成。

新 `oaza-gc-v9-20261109` 基于 v8 独立复制，仅新增 `rrzc_oaza_migration_order.h` 并修改隔离 `fs_zenfs.cc` 的 OAZA 执行分支：保持原选中 extent 集合，按源 Zone 地址、再按文件名连续迁移和调用原 ResetUnusedIOZones。其他方法仍沿用旧循环。评分、动态选区数量、分配器、恢复钩子及副本 v7/v8 均不改。跨多个源区的同一文件可以分次提交映射，需在完整账目中报告，不能声称执行开销不变。

直接编译生产排序 helper 的 UBSan 小容量用例验证：两个各含 60+40 字节有效 extent 的源区，共用 100 字节可滚动目标；按文件名交错在任一 Reset 前耗尽，按源分组能完成两次回收。另核对所有 extent 指针、同文件跨源分组、成本及选区源码字节不变。这是合成的进度反例，不冒充设备性能结果。v9 全部相关主机语法/桥/分配器检查及 53 项 Python 回归通过。

分源执行的独立客机队列位于 [recovery-batches-20261109-v1/queue.json](workspaces/recovery-batches-20261109-v1/queue.json)，监督 PID 2801813 为启动记录；先构建再执行 adapted recovery 实际 GC/重挂载。完整输入 v2 入口使用显式 `--source-batches` 和新目录，不覆盖 v1。还需这次实际设备回归及低空间完整复验，不能先认定第二个进度问题已经彻底解决。

分源执行的上述两项客机任务现已全部通过、正常退出并回收镜像；`recovery-batches-20261109-v1/功能回归最终归档核验.json` 逐字核对构建/设备 tar 共 56/38 个文件，核对真实 `OAZA-GC source batch` 日志、映射与事件账目。另将两个已匹配不可变 tar 的旧日志无损压缩，释放 351322112 字节，恢复凭据见 `旧日志无损压缩核验.json`，原 tar 和旧失败状态保持不变。

完整输入 v2 已启动在 `/home/data/xwz/rrzc-baselines/joint-related-compare-bootstrap-oaza-v2-20261109-results`，监督 PID 2803328 为启动记录；`comparison-state.json` 记录阶段，仍只运行一个 OAZA adapted、2260992 条原请求，不追加 Greedy 重复或覆盖 v1。启动时目录 205788225536 字节、可用内存 110907039744 字节、data 可用 273362776064 字节，原保护线满足。当前仍是修复验证，尚未填完整通过结论或论文性能数字。

上述“启动”为脱离终端的监督队列启动。随后共享槽位被另一任务 `joint-controlled-placement-wmig-ws-tsb1-20261109` 使用，本轮 v2 尚未启动客机，按原串行规则等待；`waiting.json` 给出真实等待原因。未关闭另一任务客机，也未叠加第二台。后续资源和目录预算仍在实际取得槽位时重新核验，不能将本次预检视为永久资源预订。

### OAZA adapted 完整修复复验通过，继续 ColdMap 两方向

`joint-related-compare-bootstrap-oaza-v2-20261109-results` 已达到 corrected_arm_verified：2260992 条请求逐条符合原输入，load/run/verify/reopen 均退出 0，内容摘要/校验和、逐阶段组成与维护账目及参考输入核对通过，OAZA deferred 为 0。客机、监督退出，临时镜像删除；旧 v1 及原六臂失败归档不变。这里只通过一个修复后的 adapted 臂；原 literal 失败没有用新结果覆盖，不能标为全六臂或全部论文复现通过。

2026 年 11 月 10 日启用 `run_coldmap_full_host.py`，结果目录 `/home/data/xwz/rrzc-baselines/joint-related-compare-bootstrap-coldmap-v1-20261110-results`，监督 PID 2845294 为启动记录。先核验 ColdMap v6 的客机构建/joint 恢复功能成功，再从该冻结副本仅构建一次相同共同客户端，两臂以同一二进制分别设置 printed/artifact-survival，串行执行各 12 窗口、2260992 条请求和完整四阶段验证。GC=20%、RR=1024、输入/观测配置保持原值，不添加 Greedy/CAZA/OAZA 重复。控制器保留真实失败归档并仅在完整退出核验后继续下一独立臂；未归档/部署/审计错误仍停止。状态为 corrected_arms_verified 前不发布两版通过。

为满足原 200 GiB 目录上限及 8 GiB 启动预留，在无客机且持保存策略维护锁时，将五个已分析失败复验的 32 MiB 以上二进制本地副本无损压缩，释放 1172660224 字节。每个原 tar 和逐文件源 SHA 均先核验，压缩件解压再核验，落盘恢复清单后才回收未压缩件；原 tar、文本错误、成功 G2/CAZA/OAZA v2 及论文文件不改。`失败二进制无损压缩.json` 位于各失败臂目录；需要逐请求/组成离线重算时，按清单解压到新的临时路径并核对 SHA。`comparison_failure.py` 现可逐字匹配登记压缩件与原 tar，仍拒绝修改/缺失证据且保留失败结论。预核验与执行凭据为 `recovery-bootstrap-20261109-v1/ColdMap整轮准入失败证据压缩预核验.json`、`ColdMap整轮准入失败证据无损压缩核验.json`。54 项 Python 回归及相关脚本语法通过。

### ColdMap 启动故障已定位，两版完整输入通过（2026-11-10）

上述队列的 printed 成功，survival 在部署前因 SSH 控制路径长度失败。旧路径加 OpenSSH 临时后缀达到 Unix socket 的 108 字节边界；新客机上独立复现得到退出 255、`unix_listener` 和 `too long`。使用短路径后 master 和 `id` 检查通过。宿主入口局部适配保留原设备、资源和镜像管理流程，并修复无归档时压缩触发的 `KeyError('archive')`；未修改原机制或论文算法。

`joint-related-coldmap-survival-v2-20261110-results` 仅复用原成功 printed 的同一二进制续跑 artifact-survival。两版各 2260992 条请求逐条符合原计划，load/run/verify/reopen 均退出 0，内容摘要与重开一致，全部阶段账目闭合，deferred=0；独立核验各 309 个归档文件、缓存/RC 边界及冻结源码通过。客机、监督退出，两轮临时镜像和旧启动失败镜像均已核验回收。59 项 Python 测试通过。

凭据见 `workspaces/comparison-20261109-v1/ColdMap两版整轮最终核验.json`，结果、启动诊断及保存策略见 [ColdMap启动修复与完整复验](workspaces/comparison-20261109-v1/ColdMap启动修复与完整复验.md)。两版业务 GC 复制分别 210580747/283139045 字节，RR 分别 944780226/972074071 字节；验证扫描和重开维护另外保留。完整负载的非平凡模拟压实步数均为 0，因此不宣称已证明预测收益。OAZA literal 的完整失败仍保留，不把这次两版成功记作完整六臂或统计性能验收。

本次启动预检：目录 204954374144 字节、可用内存 111075569664 字节、data 可用 274196295680 字节，无其他实验监督和客机。实际每臂仍重新检查资源，不将预检当资源预订。原方法默认冻结映射和评分公式不改，目前未宣布全部相关工作完成。

### OAZA literal 使用 v9 复验仍失败（2026-11-10）

`joint-related-oaza-literal-v3-20261110-results` 复用 adapted 已通过的同一 v9 二进制，不重新编译或重跑成功臂。literal 运行在第 408 个成功 Flush 批次之后报 Zone allocation failure、SIGABRT（-6）；load 成功，verify/reopen 未执行。176 个原 tar 文件逐字核验，客机和监督退出，临时镜像回收。失败结论、冻结树和原参数均保留，未生成六臂通过或收益汇总。

分源执行在前七轮已实际完成。末几轮选中数据有效比例约 95.3%，仍有更低有效量但更高字面成本的合格候选未选；7 个完成轮的原生迁移合计 2846671663 字节，不能当作包含未完成第八轮的完整业务总量。另由源码及逐条代际核对确认：`stats.reset_zones` 实为返回时仍为空的源区数，而非 Reset 命令次数；前七轮选中源实际 23 次成功 Reset，末三轮每轮 4 次，日志汇总却各为 1。源区重用后仍可高有效量反复参与迁移，不能再将此直接归为旧全局文件名交错故障。

run 组成 trace 因 abort 只有 32 字节未完成头；本轮原生日志诊断不冒充完整复制—映射—Reset 账目。详细证据和归因边界见 [OAZA字面版v9完整复验与失败分析](workspaces/comparison-20261109-v1/OAZA字面版v9完整复验与失败分析.md)。63 项 Python 回归及脚本语法通过，阶段四/五及全部冻结树哈希不变。此前要求两版分别验证后再定，主对照口径现待选择；没有擅自改 literal 公式使其跑通。

### 用户确定 adapted 为主对照，五臂集成结果完成复核

用户明确选择“修正版作主对照，字面版保留为歧义与失败分析”。该口径单独登记于 `workspaces/comparison-20261109-v1/主对照口径-20261110.json`；旧六臂协议、原树、literal 源码及失败 tar 不改写。后续主对照使用 OAZA adapted，ColdMap 两方向仍分列，literal 不进入成功主对照名单。

本次没有启动客机、重编或重跑成功臂。`verify_main_comparison.py` 只读复核 G2/CAZA/OAZA adapted/ColdMap printed/artifact-survival：每臂 2260992 条请求逐条符合计划，四阶段退出 0，内容/重开、共同输入、映射及组成账目通过；各 309 个原归档文件与本地字节逐一匹配，缓存、严格 RC 和源码身份检查通过。literal 失败 tar 另核验并引用，仍明确失败。全部冻结树及阶段四 18 份、阶段五 21 份哈希不变。

凭据为 `主对照五臂完整输入核验.json`；`主对照五臂结果/` 包含维护/吞吐、20 条逐阶段维护、10 条应用延迟指标及20条阶段末端设备空间。汇总将 source_empty_at_return 与真实阶段 Reset 事件数分列，不把返回时空源区数量当 Reset 命令数，也不把验证读维护删去。

`selected_main_full_input_verified=true` 仅代表选定五臂的单轮完整输入集成验收；原六臂历史验收仍未全部通过。G2 为放置配公共有效量 Greedy GC，并非完整 Credit/Score 联合模型；OAZA 为 15% 启动，其余 20%，装载后布局/RC 可不同，运行日期及资源交错也可能不同。本次不宣称统计性能优劣，不修改论文数字。ColdMap 非平凡模拟与实际 GC 联合证据仍未完成，`all_methods_complete=false` 保持。66 项 Python 回归及新脚本语法通过。
