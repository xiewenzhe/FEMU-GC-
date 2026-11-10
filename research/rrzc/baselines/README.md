# ZNS 相关工作独立实现

本目录管理 CAZA、完整 OAZA-GC（含 OAZA 放置）、ColdMap。三项工作分别生成独立源码与构建目录；FEMU、原机制源码、旧库及旧程序不原地替换。

用户已确定 **OAZA adapted 作主对照，literal 保留为歧义与失败分析**。配置见 [主对照口径](workspaces/comparison-20261109-v1/主对照口径-20261110.json)；原字面版源码、验证工具和失败归档继续保留。ColdMap 两方向仍分列。

选定五臂已完成只读复核及[结果汇总](workspaces/comparison-20261109-v1/主对照五臂结果/结果与边界.md)：每臂 2260992 请求、四阶段账目通过。本表为单次完整输入集成验收；G2 参考臂使用公共 Greedy GC，正式完整联合机制性能验收仍未完成。

CAZA 已通过独立编译及放置、同负载GC后自然RR、复制EIO、NoSpace恢复和重挂载功能验证。OAZA-GC的字面版和修正版均通过实际回收、重挂载后GC成本复算，另通过四层OR分配、零重叠实际层级提交和恢复后继续写入。ColdMap两种因子方向均通过三候选/六步trivial move模拟/实际GC的共同执行，独立四层用例另验证生成SST参与后续模拟。完整共同输入已通过 G2、CAZA、修复后的 OAZA adapted 和 ColdMap 两版；OAZA literal 尚未通过完整验收，全部方法及统计性能验收仍未完成。ColdMap 本轮证据见 [启动修复与完整复验](workspaces/comparison-20261109-v1/ColdMap启动修复与完整复验.md)。详细来源、歧义和验证边界见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

## 目录

|路径|内容|
|---|---|
|`isolation.py`|核验来源并生成三个独立起始副本，拒绝覆盖、软链接、硬链接及身份不符|
|`test_isolation.py`|隔离、原文件保护及失败路径测试|
|`catalog.json`|三项工作的范围、来源及尚未确认的复现条件|
|`coldmap/check_adaptive_window.py`|编译作者原版 `UpdatePredictCnt()`，仅字段后端为测试替身|
|`coldmap/check_author_algorithms.py`|提取并编译作者七个未改动函数，核验冷度聚合、模拟传播及不一致快照的循环边界|
|`common/rrzc_related_snapshot.h`|隔离算法输入、固定字节Key插值与整数溢出检查|
|`common/paper_policy_test.cc`|正文公式、两种OAZA成本、ColdMap字节加权、K=3及生成SST模拟测试|
|`common/run_native_host.py`、`common/run_device_host.py`|三种方法独立构建及联合设备入口；复用既有镜像/结果生命周期|
|`common/run_joint_validation_host.py`|等待共享客机及结果维护锁，串行执行三种方法的联合验收；可复用已完成且归档核验通过的队列任务|
|`coldmap/joint_probe.h`、`common/joint_rr_probe.h`、`common/joint_checks.py`|多候选/非零模拟/实际GC联合检查，CAZA的GC后自然RR，以及OAZA重挂载GC检查|
|`common/verify_paper_runs.py`|核验不可变归档、GC/RR分账、实际源区与候选成本；检查原CAZA及阶段四/五保护哈希|
|`common/comparison_source.py`、`common/run_comparison_host.py`|同一已核验客户端的独立重编与六臂串行负载入口；保留显式变体，支持一窗口前缀及归档后续跑|
|`common/comparison_guest.py`、`common/analyze_comparison.py`、`common/verify_comparison_host.py`|独立空设备准入、逐请求/Flush完成/内容摘要、逐阶段完整维护账目、缓存及不可变归档核验|
|`common/summarize_comparison.py`|只在最终归档核验通过后输出逐阶段维护、应用完成延迟与阶段末端设备空间；业务与验证读维护分列|
|`common/finish_comparison_host.py`|独立等待完整六臂归档退出，复核后自动汇总；失败或核验代码漂移时保留失败状态，不发布通过结论|
|`common/compact_closed_requests.py`|在无活动客机且取得维护锁时，无损压缩已归档旧请求副本；保留原tar、解压SHA256及恢复清单|
|`common/comparison_failure.py`、`common/analyze_comparison_failure.py`|逐文件核验已退出失败臂的原始归档；分辨业务/校验失败，输出不冒充完整账目的诊断时间线|
|`common/prepare_recovery_bootstrap.py`、`common/run_recovery_bootstrap_host.py`|生成新的恢复启动隔离副本，在重开写入前发布已恢复的 LSM 元数据；串行编译与功能回归，旧副本及公式保持不变|
|`common/run_bootstrap_full_host.py`|功能回归通过后，用同一客户端完整输入复验修正版 OAZA 的低空间重开；单臂修复验证，不发布六臂收益结论|
|`common/prepare_oaza_batch_order.py`、`common/run_oaza_batch_validation_host.py`|保持选区及成本不变，生成按源 Zone 连续执行迁移的新副本，并编译/回归；小容量进度反例见 `oaza_gc/migration_order_test.cc`|
|`common/run_coldmap_full_host.py`|使用恢复修复副本构建一次相同客户端，串行完整复验 ColdMap printed 与 artifact-survival；不重跑其他方法|
|`common/reliable_startup.py`、`common/run_coldmap_survival_host.py`|分离 SSH master/身份检查、缩短过长控制路径，只续跑未完成的 survival；不改变设备或论文算法|
|`common/verify_coldmap_pair_host.py`|独立核对两版完整请求、四阶段账目、原归档字节、缓存/RC 边界及冻结源码|
|`common/run_oaza_literal_full_host.py`、`common/analyze_oaza_literal_resume.py`|复用 v9 成功程序，只复验 literal；保留真实 NoSpace 失败，并按原生日志区分 Reset 事件与返回时空源区|
|`common/verify_main_comparison.py`、`common/summarize_main_comparison.py`|按用户选定口径，只读核验五个已成功完整臂并汇总；literal 失败另列，原六臂历史入口不改|
|`common/compact_failed_comparison.py`|只在无活动客机时，将已分析失败复验的大型二进制记录无损压缩；保留原 tar、解压哈希及逐文件恢复清单|
|`oaza_gc/prepare.py`、`policy.h`、`allocator.inc`、`selector.inc`|OAZA分配、零重叠层级关联提交、动态回收数量及显式成本变体|
|`coldmap/prepare.py`、`policy.h`、`selector.inc`|真实LSM元数据接线、模拟新SST及按字节冷度的成本收益选区|
|`coldmap/model_probe.h`、`oaza_gc/middle_probe.h`|真实多层DB快照的模拟验证、层级提交与恢复后继续写入；均为功能测试|
|`export_source_{host,guest}.py`|只读导出当前客机完整软件源码，临时系统客机归档后关机回收|
|`caza/prepare.py`|核验完整来源及第二阶段冻结源码，生成独立 CAZA 补丁与完整源码副本|
|`caza/check_host.py`|原生源文件语法检查与四项独立测试；可单独运行 ASan/UBSan|
|`caza/run_build_host.py`|独立客机编译与普通目录小测入口；已完成真实构建及已知ABI偏移核验|
|`caza/run_device_host.py`|严格核验成功构建归档后，复用同一程序做独立空FEMU盘小测|
|`caza/rr_probe.h`|通过真实读取达到既有RR阈值，核对迁移、源区Reset代际及计数清零|
|`caza/pressure_probe.h`|正常Flush/Compaction覆盖Active名额回退；GC用例另以真实SST及非SST填充构造空间压力|
|`caza/nospace_probe.h`|实际写到普通分配返回NoSpace，核验已提交数据，正常删除填充文件后恢复Flush|
|`caza/fault_io.cc`|仅故障用例加载；对已明确装载的FEMU命名空间注入一次pwrite EIO，普通文件不受影响|
|`caza/diagnose_guest.py`|原库符号与崩溃栈采证，不安装系统软件或修改原库|
|`test_caza_admission.py`|拒绝把链接成功、失败小测或被改写的解压日志当作设备准入|
|`workspaces/caza-v6-20261107/`|当前适配副本、补丁、来源身份、主机检查及直接I/O/RR核验|
|`workspaces/caza-v5-20261106/`|前一轮buffered最小集成验证及失败诊断记录，继续保留|
|`materials/`|只读研究材料及固定提交的作者代码；不运行其中脚本|
|`workspaces/initial-20261106/caza/`|CAZA 独立起始源码、构建及补丁目录|
|`workspaces/initial-20261106/oaza_gc/`|完整 OAZA-GC 独立起始目录|
|`workspaces/initial-20261106/coldmap/`|ColdMap 独立起始目录及原函数验证证据|

`materials/` 和 `workspaces/` 不自动纳入 Git。冻结来源及逐文件哈希保存在工作目录；新入口和测试由 Git 管理。源副本通过普通复制生成，不使用硬链接或软链接。

`initial-20261106` 下每个方法只有 ZenFS 起始文件，**不是完整 RocksDB 源码树，也不是中性的外部策略实现**；其中仍有原机制代码。CAZA 后续副本已经取得匹配的完整 RocksDB 10.7.0 源码，新增显式模式与冲突检查，但仍须通过配置和真实执行日志确认对照边界。不能将 ColdMap 自带的 RocksDB 直接当作三组共同软件版本。

## 已完成操作的核验

在仓库根目录执行：

```bash
python3 research/rrzc/baselines/isolation.py verify research/rrzc/baselines/workspaces/initial-20261106
python3 -m unittest discover -s research/rrzc/baselines -p 'test_isolation.py' -v
```

新建另一份隔离起点时必须指定尚不存在的目录：

```bash
python3 research/rrzc/baselines/isolation.py prepare \
  --output research/rrzc/baselines/workspaces/新的独立标签 \
  --upstream research/rrzc/baselines/materials/coldmap-upstream \
  --commit e03c70760371b540964dac8b3a0c2596c06cf418
```

`verify` 目前验收的是未接线的起始副本。后续策略修改必须有自己的补丁、变更清单及构建验收，不能改写起始清单来冒充无修改通过。

本轮首次准备时保护检查通过；收尾检测到既有 `recover_placement_archive_host.py` 在期间发生变化，严格复核返回退出码1，同时报告三个起始副本仍有效。差异见 `workspaces/initial-20261106/收尾复核-20261106.json`。本轮没有写该脚本，也未重写原保护清单；当前不能宣称整个工作区仍与准备时完全一致。

起始 `方法状态.json` 仍记录 `implementation_complete=false`、`runnable=false`；不改写旧清单冒充验收。`isolation.py` 本身不启动客机、不创建镜像、不格式化设备。构建和设备入口通过已有生命周期管理临时客机，拒绝与其他项目客机叠加。设备入口只操作已核验为FEMU、8GiB、64个全空Zone的本轮命名空间；已有客机及历史盘不参与。

构建必须使用已验证的 `NDEBUG` 配置匹配原库核心对象，不能仅依据 `_debug.a` 文件名或当前Make dry-run猜测原库ABI。设备入口要求成功POSIX日志、程序哈希和归档字节相互吻合。当前两层合成夹具不代表原有实验配置，也不是完整论文负载。

设备入口以 `--case basic|direct|rr|pressure|gc|fault|nospace` 区分功能用例，实际执行使用 `--detach`。非基本用例核对归档中的五份测试源码与当前协议一致；直接I/O另要求POSIX直接I/O通过，pressure/gc/nospace另要求POSIX压力夹具通过，fault另核验共享库哈希与普通文件不注入的实测日志。故障库仅装入本轮故障客户端，不用于mkfs或原机制。NoSpace不强行消耗迁移预留和元数据容量。RR后候选若由公共迁移预留池独占，按原规则回退；尚未组合验证直接I/O与RR。

回退时继续使用原机制既有程序和入口即可；本目录没有改动它们。未执行 Git 提交或推送。
