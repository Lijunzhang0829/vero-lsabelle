# Refine：补全 PLAN 后的全流程实验

**已实现并在机器 B 执行独立 PLAN → 验收 → 从零生成 → 独立 Isabelle 检查。最终通过 35/287（12.20%），3 项因模块构建失败未计分，249 项未填写；Refine 尚未全部完成。**

## PLAN 如何参与生成

1. 独立文件读写 agent 读取全部固定契约、sanitized 定义和合法 premise，逐步写入 `.vero/plan.json`。保存完整事件流和每两秒采样的变化快照；采样不保证捕获每次写入。
2. 确定 theory 分组、组内契约顺序、imports、执行顺序及理由。验收要求 287 项恰好各出现一次、依赖无环、标识合法、源 benchmark 一致；命题和 context 由固定输入提供。
3. 首次 600.00 秒仅产出 44 个模块、207 项覆盖，被拒绝，没有启动证明生成。显式续跑以该部分计划为输入，再用 478.96 秒完成；原预算完整记账。
4. 接受的计划包含 **71 个 theory、152 条直接 imports，最长导入路径含 14 个模块**。原输入是 21 个 owner 分组；新分组、理由和来源映射见 [plan.json](plan.json) 与 [module-mapping.json](module-mapping.json)。并非简单沿用 21 个文件。
5. 空证明的 Isabelle 预检查用时 152.63 秒，确认声明和导入可加载，此检查不证明任何目标。
6. 冻结计划并编译为新的 benchmark；生成器实际读取该计划，渲染真实 theory imports。检查工具包含依赖闭包，独立验收按拓扑顺序执行。生成 agent 仍是仓库级 agent，能修改多个文件；计划顺序是指令，不是禁止跳跃的调度器。

核对确认 287 个契约的 ID、原命题、上下文及来源保持不变，仅改变模块分配和排列。没有导入原始 Refine 人工证明，也没有复用此前生成的 56 个证明。本版本支持跨 theory 定理引用，尚未实现规划新增共享顶层辅助引理。

## 结果和比较

| 实验 | PLAN agent 时间 | 生成时间 | 从何开始 | 独立通过 |
| --- | ---: | ---: | --- | ---: |
| 之前首轮，无 PLAN | 0 | 1,800.01 秒 | 空证明 | 39/287 |
| 本轮，有 PLAN | 1,078.96 秒 | 1,800.01 秒 | 空证明 | 35/287 |
| 之前第二轮补跑 | 0 | 累计 3,596.88 秒 | 继承首轮 39 项 | 56/287 |

本轮模型为 `gpt-6-astra`、medium；独立验收另耗时 200.05 秒。生成 agent 在预算到时被终止，留下 38 个候选。最终 35 项通过，与旧首轮重合 31 项；本轮独有 4 项、旧首轮独有 8 项，见 [comparison.json](comparison.json)。本轮并未提升总通过数；单次实验、额外规划预算和此前网络干扰不支持 PLAN 效果的因果结论。生成与首次 PLAN 的完整 token 使用未返回，费用未知。

最终文本检测到一处跨生成模块定理名称引用：`c0065 deletedIRQHandler_corres`（`Vero_FinaliseSlot`）引用 `c0076`（`Vero_IrqPrimitives`，已独立通过）。验收日志包含 c0065 的 audit，但其所属模块后来失败，因此 c0065 不计为独立通过。名称扫描不能完整描述自动化隐式使用的事实。

## 当前卡在哪里

- `c0071 postCapDeletion_corres`：最终 Isabelle 报告 12 个未解子目标，涉及各种 capability 分支下的 `global.postCapDeletion` 对应关系，见 [原始错误](finalise-failure.log)。这是已定位的直接证明失败。
- `c0062 arch_postCapDeletion_corres`、`c0065 deletedIRQHandler_corres`：检查先产生 audit，但受同 theory 的 c0071 失败影响，最终不计分。三个 `build_error` 并非三个各自独立的数学失败。
- `c0162 setCurThread_corres`：有 4 次候选提交记录，最后撤回为 `oops`，最终未填写；见 [逐契约记录](ledger/contracts/c0162.json)。
- 其余 248 项未解决契约没有捕获到填写候选后调用检查的记录；这表示尝试历史未知，不等于模型从未阅读或思考过它们。

共保存 46 次检查提交，1 次没有完成结果，保留为中断记录，见 [全量 ledger](ledger/ledger.md)。当前中间快照保存本模块的 proof bodies 和 source，尚未保存每次检查时完整导入闭包的 proof bodies，不能保证精确重放每次跨模块中间检查。最终 artifact 和独立验收源码完整保存。

## 记录、测试与清理

- [PLAN 首次完整轨迹](traces/plan-first/agent_events.jsonl)、[续跑轨迹](traces/plan-continuation/agent_events.jsonl)、[生成轨迹](traces/generation-events.jsonl)，以及同目录的规划快照。
- [接受计划](plan.json)、[冻结 benchmark](benchmark.json)、[生成 artifact](artifact.json)、[独立验收](evaluation.json)、[预算与指标](metrics.json)、[配置和实现绑定](pipeline-binding.json)。
- 本地完整原始结果：父工作树 `reports/vero-isabelle-refine-plan-20260916/`；机器 B：`/data/zljj/vero-runs/refine-plan-001/results/`。首个流水线为 `pipeline`，接受计划并生成的为 `pipeline-02`。
- B 上 29 项后端测试全部通过，包括真实 Isabelle 跨 theory 引用。新增 PLAN 续跑用例后，本地共 30 项，25 项通过、5 项因未配置 Isabelle 测试路径跳过。真实测试见 [backend-tests.txt](backend-tests.txt)。
- 本轮运行代码对应本地提交 `c687fd4`（PLAN 主实现 `3165cd7`）；B 使用限定文件同步，不能用 B 的旧 git HEAD 代替实际代码身份。流水线保存了实现摘要。
- 临时认证源副本、agent 认证目录和专用 worker 已删除，主 `sel4-l4v` 容器仍运行，见 [cleanup.json](cleanup.json)。

后续应分别测量规划质量、生成实际利用依赖的程度，以及多次同预算实验下的通过率。此轮完成了流程补全与真实运行，尚未证明 PLAN 的性能收益。
