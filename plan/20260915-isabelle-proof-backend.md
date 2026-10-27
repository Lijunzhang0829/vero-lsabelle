# 机器 B：Vero 的 Isabelle proof 模式实现计划

状态：用户已批准实施。Isabelle proof-mode 后端与机器 B 环境已部署，首轮 Refine 试点已完成，独立验证 39/287 个正向证明；结果与限制见 `reports/refine-machine-b-20260915/README.md`。原计划保留如下；当前实现及限制见 `docs/isabelle.md`。

## 1. 目标与范围

默认首轮目标是在 seL4/l4v 上复现 Vero 的 proof 模式：给定固定实现和规格，由一个可替换的代码代理读取整个任务工程、填写证明、调用 Isabelle 检查并根据诊断继续修改。先使用小型 Isabelle 测试工程验证机制，再接入 seL4/l4v 的有限任务集。用户已收到任务范围选项；若选择移植原始 Vero 基准，调整第 6 阶段的输入来源。

复现对象是 Vero 的生成与独立评估协议。首轮不包括 codeproof、43 个 Lean 基准的全面移植，也不把现有 Repository Planner、GRA 或 stepwise 搜索加入 Vero 基线。代理在一次调用内可以自行分解证明、引入槽位内的局部辅助结论、搜索允许的事实及重试。

Vero proof 模式同时允许证明规格和给出规格不成立的正式反证。这两条路径都纳入适配，报告分别统计 proved、disproved 与 resolved，避免把审计发现当成正向证明。

## 2. 已核实的起点

- 上游 Vero：`https://github.com/sunblaze-ucb/vero.git`，本地快照 `0a7325df9e9e6dbc275c0ad483b3d1cbe38d9b09`，工作目录干净。
- 核心实现：`generation/runner.py`、`generation/agents/base.py`、`generation/{sandbox,extractor,render}.py`、`evaluation/runner.py`、`cli_iterate.py`。
- B 上的主环境：`/data/zljj/sel4-dev`；Isabelle2024 `74b2d1278b57797572abe5842e318d17ed131c55`；l4v `00d9073f70d064b31b4b1fad8a0e7086bb258007`，包含已同步的本地修改；seL4 `daa0dfb1470c5ffbf13b3778f93111679574e80c`。
- `sel4-l4v` 容器运行正常；此前已验证 HOL 证明和 AInvs 加载。这个验证只说明基础工具链可用，不证明实验输入已去除参考证明。
- 主机镜像清单摘要为 `8e7ee430…`，B 的 Docker 显示其中的配置摘要 `65933e97…`；清单和 19 个文件层已确认一致。实验身份应记录这两种摘要和实际内容，不直接跨 Docker 版本比较展示 ID。
- B 上尚未发现 Vero 工作目录；宿主 Python 为 3.8.10，Vero 要求 Python ≥3.10。驱动使用独立 Python 3.11 环境，Isabelle 继续使用已核验的容器工具链。
- B 的 `regen` 是主分支平铺布局，当前本地工作树已有分目录重构。复用模块时必须注明来源提交及必要补丁，不能假定 B 已安装当前工作树的新接口。

## 3. 要保持的 Vero 行为

```text
固定 benchmark + 配置
  → 生成新 sandbox 和任务说明
  → Agent 编辑证明槽位、运行 Isabelle、读取诊断
  → 提取 artifact.json
  → 用固定 benchmark + artifact 重建独立 evaluation 工程
  → Isabelle 检查 + 定理/依赖审计
  → report.json / report.md
  → 按配置结束，或进入下一轮
```

| Vero 行为 | Isabelle 对应实现 |
|---|---|
| 固定实现、接口和规格 | 固定 `.thy` 语义文件、声明、locale 上下文及 imports |
| 每个规格的 prove/disprove 槽位 | `P` 与 `¬P` 的两个受控证明槽位，恰好一个可计分 |
| 代理编辑槽位并自行调用 Lean 工具 | 相同代理协议，工具与说明改为 Isabelle 的 theory/session 检查 |
| 从槽位提取 artifact | 由冻结的槽位清单提取；缺失、改名及额外槽位分别记录 |
| 新工程重渲染评分 | 只从可信模板和产物重建；不信任代理改过的 ROOT、imports、设置或规格 |
| Lean 公理审计 | 定理存在及命题匹配、skip-proof/oracle 审计、冻结信任边界检查 |
| 可配置外层迭代 | 保留 off/eval/self、反馈开关、预算、停止规则；续接产物与会话分块分别配置 |

以锁定提交的代码为准，而非照搬可能滞后的教程。例如 `resume` 在现有实现中默认关闭；开启反馈不代表自动继承上轮产物。成本计量按后端实际返回值记录，未知成本不记为零。

## 4. 实现布局

建议在 B 新建 `/data/zljj/vero-isabelle`，检出上述 Vero 快照后使用 `feat/isabelle-proof-backend` 分支。主 seL4 环境作为冻结来源，生成与评分任务使用独立目录、进程和缓存。

在 Vero 中增加最小证明器后端接口，默认仍为 Lean。拟新增的职责如下，具体文件名在实现时依现有模块边界微调：

```text
src/vero/backends/base.py
src/vero/backends/isabelle/
  benchmark.py       # 固定规格、上下文和槽位清单
  sandbox.py         # 生成工程、受控工具入口
  render.py          # 可信模板重建
  extractor.py       # 精确提取证明槽位
  checker.py         # Isabelle 执行、超时、结构化诊断
  audit.py           # 定理、命题、oracle 和信任边界
  evaluation.py      # 按规格评分及汇总
templates/instruction/isabelle/
conf/backend/isabelle.yaml
tests/isabelle/
```

优先复用 Vero 的代理接口、日志、预算与迭代层；复用现有 Isabelle 工具的进程管理、上下文构造和检查机制。复用不包括现有系统的规划决策、目标专用提示、已生成解答或成功检查点。

## 5. 必须先解决的 Isabelle 边界

### 5.1 固定命题与上下文

由可信准备程序解析理论边界，保留 types、definitions、imports、locale、fixes、assumes、type classes 等上下文；不能仅用正则裁剪 `by` 后面的文本。

prove/disprove 必须针对同一个闭合的 HOL 规格命题。对于带元层全称量词、假设或 locale 的 seL4 契约，先构造经 Isabelle 检查的规格封装及其与原始契约的对应关系，再生成 `P`/`¬P`。不能简单把 `shows` 后的文本取反，也不能通过增加假设使结论成立。不支持的声明形态明确拒绝或列为适配阻塞。

### 5.2 未完成证明不能成为合法前提

优先将未填槽位渲染为不导出定理的未完成状态，例如受控 `oops`；未填槽位不因工程编译返回零就得分。若某种兼容模式必须用 `sorry` 保留声明，则所有依赖它的证明都必须通过传递 skip-proof 审计后才能计分。具体策略在首轮样例中验证，再固定到配置中。

### 5.3 参考证明与缓存隔离

现有主容器包含 l4v 原始证明和 AInvs/Refine 等预构建 heaps，不能直接作为生成代理可任意浏览的工作环境。为实验制作净化运行环境：只暴露固定实现、规格、声明允许的上游库及其缓存；隐藏目标参考证明、目标旧 heaps、Git 历史、旧结果和解答目录，也不向代理暴露主机 Docker socket。

复用父 session 必须先确认它不包含目标证明，且在报告中声明其为可信输入。例如 Refine 任务可以使用明确冻结的允许上游，不能加载含目标结论的旧 Refine heap。生成与评分使用独立 `ISABELLE_IDENTIFIER` 和明确的 imports/session 根，避免自动搜索到主环境缓存。

### 5.4 独立评分

评分器重建固定 theory/ROOT，只插入允许的证明体。检查目标定理实际存在，其导出的命题及假设与冻结契约一致；使用 Isabelle 提供的 `Thm_Deps.has_skip_proof`、`Thm_Deps.all_oracles` 等接口检查传递依赖，并对照已记录的可信理论/公理边界。

未审计的 `ML`、`setup`、`oracle`、新公理、越界 theory 命令等不进入评分工程。词法筛查只是前置检查，最终以 Isabelle 的定理证据和冻结环境为准。固定命令的 Sledgehammer 等工具可以作为代理接口提供，但结果必须转化为可重放的正式证明。

## 6. 分阶段交付与验收

| 阶段 | 工作与交付物 | 完成条件 |
|---|---|---|
| P0：固定复现协议 | 锁定 Vero 快照、任务边界、后端接口、模型/预算/迭代配置；在 Vero 的 `plan/` 登记本计划 | 协议明确区分上游行为与 Isabelle 必需适配；没有隐含默认实验预算 |
| P1：部署与可信环境 | B 上安装独立驱动；冻结工具链；生成净化运行环境和输入清单 | 驱动与 Isabelle 可通信；代理无法读取目标参考解答或目标旧缓存 |
| P2：不调用模型的完整链路 | 实现 benchmark → sandbox → artifact → 新工程评分；用已知正确/错误的测试产物验证 | 正确证明通过；错误、未填、重复、修改规格、sorry 依赖和新增 oracle 等样例按预期拒绝 |
| P3：单次代理生成 | 接入一个配置指定的 Vero Agent；适配 Isabelle 说明及检查工具 | 在小型多 theory 工程中完成真实模型生成，保存完整日志和独立评分证据 |
| P4：迭代与恢复 | 接入 eval/self/反馈/产物续接，保留原配置语义；实现中断和预算记录 | 能重放产物；超时不丢日志，恢复不重置已消耗预算；网络故障单独分类 |
| P5：seL4/l4v 试点 | 从既有支持范围优先挑选约 2–3 个 theory、10–20 个固定契约；先确认上下文与依赖可净化 | 输出逐契约 proved/disproved/unresolved、模块构建结果、耗时与 token；不以少数目标通过代替整体完成 |
| P6：扩展评估 | 按试点结果扩展到完整选定模块/session；保留 single-run 与反馈迭代对照 | 配置、环境及预算可复现；报告全部覆盖率、失败原因与是否完成整个任务集 |

小型工程包含跨 theory 依赖、局部辅助证明、locale、一个可正式反证的错误规格及一个未完成依赖案例。这些是验证适配器正确性的工程测试；正式实验的生成代理不能得到这些测试的参考解答。若需要与论文基准直接对应，可将 BankLedger 列为后续单独的语义移植阶段，而不是宣称 seL4 结果复现了原论文分数。

## 7. 最终交付

- B 上可运行的 Isabelle 后端及固定配置；Lean 后端兼容性回归通过。
- 净化后的输入清单、可信上游及缓存身份、生成/评分隔离配置。
- 每次运行的 `artifact.json`、`agent_events.jsonl`、`report.json`、`report.md`、迭代与预算记录。
- 独立重放命令以及逐契约的 Isabelle 检查证据。
- 试点结果和扩展范围，不预先承诺目标证明成功率。

模型、首轮任务集和预算写入审核后的配置；本轮不启动收费生成。计划审核通过后再开始实现。实施分支拟为 `feat/isabelle-proof-backend`，可与计划一并确认；Vero 的 `CLAUDE.md` 明确要求编码前确认分支，且禁止未经确认推送。

## 8. 本轮查阅依据

- `/home/lijun/seL4-docker-main/vero/CLAUDE.md`、`README.md`、`TODO.md`、`WORKLOG.md`。
- Vero 的 `src/vero/generation/runner.py`、`agents/base.py`、generation/evaluation README、`cli_iterate.py`、`conf/iterate/` 和 instruction 模板。
- 当前工作树 `regen/execution/isabelle_node_checker.py`、`regen/preparation/{semantic_base_builder,environment_gate}.py` 及实现文档。
- Isabelle2024 `src/Pure/thm_deps.ML` 和 `thm.ML` 中实际提供的审计接口。
- 机器 B 的只读状态检查及上一轮重建验证记录。
