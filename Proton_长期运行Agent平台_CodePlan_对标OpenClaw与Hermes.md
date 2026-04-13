# Proton 长期运行 Agent 平台 Code Plan（对标 OpenClaw / Hermes 的半年发展方向）

> 目标：以 **Proton** 为主线补齐底座，把它从“可视化树形编排 + 多来源 Agent 适配 + Portal/Copilot”升级为 **长期运行 Agent 平台**：具备明确的 **控制平面（Control Plane）**、可替换 **执行平面（Execution Plane）**、可证明的 **治理平面（Safety/Observability/Eval Plane）**、以及工程化的 **知识/记忆/技能平面（Knowledge Plane）**。  
> 对齐参考：`AI智能体未来半年发展方向_基于OpenClaw与HermesAgent.md` 中的 8 大延展方向（控制平面产品化、执行后端、技能治理、多层记忆、上下文工程、轨迹评测、多前端一致性、协议生态）。

---

## 0. 当前基线（Proton 现状速记：用于差距对齐）

**已具备（保留并强化）**

- 编排内核：`src/core/tree_executor.py` + `ExecutionContext` + `RoutingStrategy（含 intent/coordinator 等）`
- 多来源 Agent 适配：`src/adapters/*`（native/builtin/coze/dify/doubao/autogen/workflow）
- 工具/插件雏形：`src/tools/*`、`src/plugins/*`（MCP/Skill/RAG）
- 超级入口雏形：`src/portal/*`（意图路由 + 记忆 + workflow 调度 + synthesis）
- UI：`ui/`（ReactFlow 编排 + ExecutionPanel SSE）
- 存储抽象：`src/storage/persistence.py`（sqlite/file/postgres）
- **[新] 治理与沙箱层：`src/execution/backends/*` 提供了 Docker 隔离沙箱与 Local 降级机制，废弃了原有的同进程 `exec()` 风险执行。**
- **[新] 上下文并发隔离：在 `tree_executor.py` 和 `context.py` 中实现了 `create_child_context(isolate=True)` 与 `merge_isolated_context()`，彻底解决了 Parallel 与 Intent 路由中的状态竞态污染。**
- **[新] 技能程序化学习：`ArtifactFactoryService` 已经打通了通过 LLM 直接生成真实 Python Skill 脚本的流程，实现了“对话 → 总结 → 自动写代码落盘”的学习闭环。**

**关键缺口（按“长期运行平台”要求）**

1. ~~治理平面缺失：`requires_approval/is_dangerous` 未形成后端强制门禁；`code tool` 仍 `exec()`；缺统一 policy/审计/trace。~~ (**已部分修复：沙箱已落地，但统一审批拦截门禁（Gate）和审计日志（AuditLog）仍需进一步完善 UI 联动。**)
2. ~~执行平面不分层：工具执行基本在同进程/同机；缺可替换 backend（local/docker/ssh/serverless）与资源隔离。~~ (**已修复：已实现 Backend 抽象并落地 DockerBackend。**)
3. **控制平面尚未产品化**：session/identity/channel/workspace/toolset/skill/job 等未统一建模，迁移/导出/审计薄弱。
4. **评测与轨迹闭环缺失**：无 trajectory_replay、benchmark、回归门禁。（*注：目前 TrajectoryPool 已实现，但主要用于触发学习，尚未用于离线回放评测*）
5. **工具面割裂**：PluginRegistry（MCP/Skill/RAG）与 BuiltinAgentAdapter 的 tool calling 两套体系尚未完全统一。
6. **上下文工程偏弱**：压缩/缓存/引用/谱系缺系统化（影响成本与稳定性）。

---

## 1. 目标架构（半年内落地的“4 平面”参考实现）

> 目标是“像 OpenClaw/Hermes 那样先进”，但以 Proton 的优势（编排 + 可视化 + 多适配）为核心做增量重构。

### 1.1 平面划分与职责

1. **Control Plane（控制平面）**：长期状态与治理对象  
   - Entities：Identity / Session / Channel / Workspace / Toolset / SkillPackage / Job（cron）/ Release（published workflow）  
   - 能力：配置优先级、导出导入、审计日志、权限与策略绑定
2. **Execution Plane（执行平面）**：统一工具执行与隔离  
   - ToolExecutor：工具统一入口（含 approval gate、资源限制、后端路由、artifact 管理）
   - Backends：Local / Docker / SSH（优先）/ Serverless（后续）
3. **Governance Plane（治理平面）**：安全 + 观测 + 评测  
   - PolicyEngine（allow/deny/approval required）
   - Auditing（不可抵赖的执行记录）
   - Tracing（结构化 events + metrics）
   - Trajectory & Replay & Benchmark（回归门禁）
4. **Knowledge Plane（知识平面）**：记忆/技能/上下文工程  
   - MemoryRouter（bounded + session search + external providers）
   - Skill Registry（签名/锁定/来源/回滚/灰度）
   - ContextEngine（prompt assembly/压缩/缓存/引用/谱系）

### 1.2 与现有代码的映射（重构策略：增量、不推倒）

| 目标模块 | 现有模块复用点 | 新增/重构点 |
|---|---|---|
| Control Plane | `storage/persistence.py` 可复用做 entity 存储 | 新增 entity models + repos + migration/export |
| Execution Plane | `tools/*`、`plugins/*` 提供工具定义 | 新增 ToolExecutor + Backend + Sandbox + Artifact store |
| Governance Plane | `ExecutionEvent`、SSE 通道可复用 | 新增 PolicyEngine + Approval API + Trace store + Replay harness |
| Knowledge Plane | `portal/memory.py` 可复用部分策略 | 新增 MemoryRouter + SessionSearch + ContextEngine + Skill governance |

---

## 2. 代码层总体改造：目录结构与关键接口草案

> 下面给出建议的目录与“最小可实现接口”。实施时以 PR 为单位逐步合入。

### 2.1 新增目录（建议）

在 `src/` 下新增（或重命名）：

```
src/
  control_plane/
    models.py
    repos.py
    services.py
    migrations/
  execution/
    tool_executor.py
    backends/
      base.py
      local.py
      docker.py
      ssh.py
    sandbox/
      policy.py
      filesystem.py
      network.py
      resources.py
    artifacts/
      store.py
  governance/
    policy_engine.py
    approval.py
    audit_log.py
    tracing.py
    trajectories/
      schema.py
      recorder.py
      replay.py
      benchmarks.py
  knowledge/
    memory/
      router.py
      bounded.py
      session_search.py
      providers/
        base.py
    context_engine/
      assembly.py
      compression.py
      caching.py
      references.py
    skills/
      registry.py
      lockfile.py
      verifier.py
      security_scan.py
```

### 2.2 统一 Tool 抽象（合并 builtin/system/plugin 三套工具面）

新增 `execution/tool_executor.py` 中的统一工具协议（建议以现有 `plugins.registry.Tool` 为基础扩展）：

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    parameters_schema: dict
    source: Literal["builtin", "system", "plugin_mcp", "plugin_skill", "plugin_rag"]
    risk: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = False
    backend_hint: Optional[str] = None  # e.g. "docker", "local"

class ToolCall(BaseModel):
    tool_name: str
    arguments: dict
    call_id: str

class ToolResult(BaseModel):
    call_id: str
    ok: bool
    output: Any
    artifacts: list[ArtifactRef] = []
    error: Optional[str] = None
```

将下面三类统一转换成 `ToolSpec`：

- Builtin tools（`BuiltinToolDefinition`）→ ToolSpec(source="builtin")
- System tools（`SystemToolRegistry`）→ ToolSpec(source="system")
- Plugin tools（`PluginRegistry.get_tools_for_agent(agent_id)`）→ ToolSpec(source="plugin_*")

### 2.3 ToolExecutor：policy/approval/backend/trace 的统一入口

```python
class ToolExecutor:
    def __init__(self, policy: PolicyEngine, backends: BackendRegistry,
                 audit: AuditLog, tracer: Tracer, artifact_store: ArtifactStore):
        ...

    async def execute(self, *, session_id: str, agent_id: str, tool_call: ToolCall) -> ToolResult:
        # 1) policy check (allow/deny/approval-required)
        # 2) if approval required -> raise ApprovalRequired(call_id, ...)
        # 3) choose backend (tool.backend_hint or policy routing)
        # 4) run backend with sandbox limits
        # 5) record audit + trace
        ...
```

> 这一步是“先进性补齐”的关键：把 Proton 从“工具能跑”升级为“工具可治理、可审计、可回放”。

### 2.4 Approval API（后端强制门禁）

新增 API（建议）

- `POST /api/sessions/{session_id}/approvals` 创建审批请求（后端在遇到 ApprovalRequired 时写入）
- `POST /api/sessions/{session_id}/approvals/{approval_id}:approve|deny`
- SSE 事件：`approval_required`、`approval_resolved`

并在 UI ExecutionPanel/PortalChat 中支持弹窗确认或命令行确认。

### 2.5 Execution Backends（local/docker/ssh）

定义统一 backend 接口：

```python
class ExecutionBackend(Protocol):
    name: str
    async def run_shell(self, command: str, *, cwd: str, env: dict, limits: ResourceLimits) -> RunResult: ...
    async def run_python(self, code: str, *, cwd: str, limits: ResourceLimits) -> RunResult: ...
    async def fetch_url(self, url: str, *, limits: ResourceLimits) -> FetchResult: ...
```

落地顺序：

1. **LocalBackend**：复用现有实现（但必须受 limits 控制，统一日志/exit_code/artifacts）
2. **DockerBackend（优先）**：用于替换 `exec()` code tool 和高风险 shell（隔离文件系统/网络/资源）
3. **SSHBackend**：用于企业场景（受控 bastion/跳板机）执行

### 2.6 Trace / Audit / Trajectory（结构化记录 + 回放）

#### Trace 事件（面向线上可观测）

新增事件类型（在现有 `ExecutionEventType` 基础上扩展并版本化）：

- `llm_call_start/llm_call_end`（model、tokens、latency、cache_hit）
- `tool_call_start/tool_call_end`（tool_name、risk、backend、artifacts）
- `approval_required/approval_resolved`
- `memory_read/memory_write`
- `context_compressed/context_snapshot_frozen`

建议：后端生成 JSON Schema，前端通过生成脚本生成 TS 类型，避免不同步。

#### Trajectory（面向回放/评测）

Trajectory 是“更严格、可回放”的记录格式：固定输入（含工具模拟）、固定模型版本（或记录 prompts+outputs）、固定随机性（temperature=0）。

最小 schema（示例）：

```json
{
  "trajectory_id": "...",
  "task": {"input": "...", "expected": "..."},
  "steps": [
    {"type": "llm", "prompt_ref": "...", "response": "..."},
    {"type": "tool", "name": "...", "args": {...}, "result": {...}},
    {"type": "approval", "decision": "approve"}
  ],
  "final": {"output": "...", "success": true}
}
```

---

## 3. 里程碑与 PR 级拆解（半年、可并行）

> 每个 PR 都给出“涉及文件/接口/验收标准”。建议以 2 周迭代节奏推进。

### M1（第 1-6 周）：治理与执行统一（先“安全可控 + 可观测”）

#### PR-01：统一 ToolSpec + ToolExecutor（不改业务逻辑先接管执行）

**改动**

- 新增：`src/execution/tool_executor.py`、`src/governance/policy_engine.py`、`src/governance/audit_log.py`、`src/governance/tracing.py`
- 改造：
  - `src/adapters/builtin.py`：工具执行不再直接执行，改为 `tool_executor.execute(...)`
  - `src/tools/registry.py`：system tool 输出 ToolSpec（含 requires_approval/risk）
  - `src/plugins/registry.py`：plugin tools 输出 ToolSpec

**验收**

- builtin agent 的所有 tool calls 都会产生 `tool_call_start/end` trace
- 工具执行失败可定位（exit_code/stderr）

#### PR-02：Approval Gate（后端强制审批）

**改动**

- 新增：`src/governance/approval.py` + storage collection `approvals`
- API：新增 approval endpoints（见 2.4）
- SSE：ExecutionEvent 增加 `approval_required/resolved`
- UI：ExecutionPanel 支持审批弹窗；PortalChat 支持审批提示

**验收**

- `ShellTool.requires_approval=True` 的调用在未审批前不会执行
- 审批决定进入 audit log（who/when/what）

#### PR-03：Code tool 沙箱化（DockerBackend v0）

**改动**

- 新增：`src/execution/backends/docker.py`
- 改造：builtin `code` 工具改为 docker 执行（挂载 workspace 子目录 + 禁网/限资源）

**验收**

- code 执行不可访问宿主敏感路径
- 超时/内存限制生效

#### PR-04：事件协议版本化 + 前后端类型同步

**改动**

- 后端导出 `execution_event.schema.json`
- 前端生成 `ExecutionEvent` TS types（CI 校验）
- 修复前端：补齐 `intent_routing` 事件类型、删除重复 case

**验收**

- 后端新增事件不会导致前端 silently ignore（CI fail）

---

### M2（第 6-12 周）：控制平面产品化 + 执行后端可替换 + 并行隔离

#### PR-05：Control Plane Models（Session/Identity/Workspace/Toolset）

**改动**

- 新增：`src/control_plane/models.py`（最小实体）
- 新增：repos/services（CRUD + 审计）
- API：  
  - session：创建/恢复/归档/导出  
  - workspace：绑定路径/后端/配额  
  - toolset：按 session/agent 绑定的工具与策略

**验收**

- Portal/Workflow run 都能显式关联 session_id
- session 可导出（json 包含 configs、memory、skills 绑定信息）

#### PR-06：Execution Backends 扩展（SSHBackend v0）+ Backend 路由策略

**改动**

- 新增：`src/execution/backends/ssh.py`
- PolicyEngine 支持：按 tool risk / workspace / channel 决定 backend

**验收**

- 同一 workflow 可配置“shell 在 ssh 执行、code 在 docker 执行”

#### PR-07：并行执行上下文隔离与合并策略

**改动**

- 改造 `TreeExecutor.parallel/intent priority group`：  
  - 并行 child 使用 `context.snapshot()`（深拷贝共享 state 或 copy-on-write）
  - 并行结束合并 `merge_child_contexts(strategy=...)`

**验收**

- 并行 children 不会互相污染 messages/shared_state（可写单测）

#### PR-08：统一工具面（Plugin tools 进入 builtin tool calling）

**改动**

- BuiltinAgentAdapter 生成 OpenAI tools schema 时，合并：builtin + system + plugin tools
- ToolExecutor 支持 plugin handler（MCP/skill/rag）统一执行入口

**验收**

- 为某个 agent 配置 MCP tool 后，builtin agent 能直接 function call 使用

---

### M3（第 12-24 周）：记忆分层 + 上下文工程 + 轨迹评测闭环（先进性“追上”）

#### PR-09：MemoryRouter（bounded + session search）

**改动**

- 新增：`knowledge/memory/router.py`、`bounded.py`、`session_search.py`
- bounded：将 PortalMemory 的“重要事实”写入 bounded memory（有长度上限 + 冻结快照注入）
- session search：SQLite FTS5（或向量）对话全文检索 + 摘要注入（按需）

**验收**

- 不增加固定上下文太多的情况下，能“回忆”历史讨论点
- 支持 memory 写入扫描（注入模式检测）

#### PR-10：ContextEngine（压缩/缓存/引用/谱系）

**改动**

- 新增：`knowledge/context_engine/*`
- Prompt assembly 分层：system、policy、bounded memory、skill index、retrieval snippets、task、recent turns、tool results
- 引用机制：长工具输出落 artifact store，仅注入引用 + 摘要
- Compression lineage：压缩前后可追溯（用于 replay）

**验收**

- 长会话 token 成本可控、成功率不明显下降
- 可追溯“某条规则/记忆来自哪里”

#### PR-11：Trajectory Recorder + Replay Harness + Benchmark Suite（最小闭环）

**改动**

- 新增：`governance/trajectories/*`
- recorder：把线上 session 抽样为 trajectory（脱敏）
- replay：固定模型/固定工具模拟回放
- benchmark：10-20 条高频任务基准（含安全策略用例）
- CI：合并前跑 replay/benchmarks（阈值门禁）

**验收**

- 重要 workflow/skill 改动不会悄悄退化（CI 报警）

#### PR-12：Skill 治理工程化（签名/锁定/来源/灰度/回滚）

**改动**

- 新增：`knowledge/skills/registry.py`、`lockfile.py`、`security_scan.py`
- 安装流程：下载/解包 → 扫描 → 生成 lockfile（hash）→ 记录来源与信任级 → 绑定到 toolset
- 灰度：按 session/channel/用户组启用

**验收**

- 技能可回滚到指定版本
- 未信任来源技能默认不可执行高风险工具

---

## 4. 关键设计细节（决定“先进性”的几个点）

### 4.1 安全默认：把“策略”做成代码，不靠 prompt

最低要求（强制落地）：

- 所有高危工具（shell/code/email/web download）默认 requires_approval
- 所有工具执行都必须经过 PolicyEngine（allow/deny/approval/backend routing）
- 执行环境隔离：code 必须沙箱；shell 必须可控 cwd + 限制网络/资源

### 4.2 上下文工程：冻结快照 + 引用机制 + 压缩谱系

对标 Hermes 的“prefix cache 友好”策略：

- bounded memory、skill index、policy 作为稳定前缀（冻结快照）
- 动态检索结果与工具输出用“引用+摘要”，减少前缀抖动
- 每次压缩记录 lineage，支持 replay

### 4.3 执行平面：后端可替换的价值

落地顺序建议：

1. docker（隔离最直接，解决 exec 安全）
2. ssh（企业可用性）
3. serverless（成本与弹性，后续）

统一观测输出（stdout/stderr/exit_code/artifacts）是多后端的前提。

### 4.4 轨迹与评测：让平台可持续迭代

没有 trajectory/replay/benchmark，就很难长期迭代（每次改 prompt/策略/模型都会产生非线性回归）。

最小闭环：

- recorder（线上采样）→ sanitizer（脱敏）→ replay（离线回放）→ benchmarks（阈值门禁）→ CI gating

---

## 5. 实施清单（按“人天”优先级排序）

> 你可以据此拆到 Jira/飞书任务。

P0（必须先做，1-6 周）

- ToolExecutor + PolicyEngine + AuditLog + Trace（统一入口）
- Approval API + UI 支持（后端强制门禁）
- DockerBackend 替换 code tool（移除进程内 exec）
- 事件协议版本化 + TS 类型生成

P1（6-12 周）

- Control Plane entities（Session/Workspace/Toolset）
- Backend routing（按风险/通道/工作区）
- 并行隔离与合并
- Plugin tools 统一进入 tool calling

P2（12-24 周）

- MemoryRouter + SessionSearch
- ContextEngine（引用/缓存/压缩谱系）
- Trajectory + Replay + Benchmark + CI 门禁
- Skill 治理（锁定/来源/灰度/回滚）

---

## 6. 验收标准（平台级 Definition of Done）

达到“长期运行平台”的最低可用门槛（建议作为半年 OKR 验收）：

1. **安全**：高危工具默认必须审批；code 执行在沙箱；所有工具执行可审计可追溯
2. **稳定**：长会话成本可控（压缩/引用/缓存）；并行执行不互相污染
3. **可观测**：统一 trace 事件可检索；关键失败可定位（LLM/tool/approval）
4. **可迁移**：session + memory + skills + toolset 可导出/导入
5. **可迭代**：有 replay/benchmark，核心变更有 CI 回归门禁

---

## 7. 对齐“未来半年方向”映射表（确保先进性跟进）

| 发展方向（调研） | Proton 对应落地项 |
|---|---|
| 控制平面产品化 | PR-05（Session/Workspace/Toolset/Job/导出导入/审计） |
| 执行后端与沙箱分层 | PR-03（Docker）、PR-06（SSH）、ToolExecutor/Backends |
| 技能治理（程序化记忆） | PR-12（锁定/来源/灰度/回滚）、统一工具面 PR-08 |
| 多层记忆体系 | PR-09（bounded + session search + router） |
| 上下文工程 | PR-10（冻结快照/引用/压缩谱系/缓存） |
| 轨迹与评测闭环 | PR-11（trajectory/replay/benchmark/CI gating） |
| 多入口一致语义 | M2 Gateway/Channels（Portal 升级为网关 + session 语义统一） |
| 协议生态（MCP/IDE） | PR-08（MCP tools 统一到 tool calling），后续可扩展 ACP |

---

## 8. 下一步（你确认后我可以继续细化）

为了把 plan 进一步“工程化可执行”，需要你确认 3 个关键约束（不影响主线，但影响实现细节）：

1. **部署形态**：优先本地（单机）还是优先云端（VPS/K8s）？（决定 docker/ssh/serverless 的优先级）
2. **安全策略级别**：审批是“默认全开”还是按 workspace/channel 分级？（决定 PolicyEngine 初始规则）
3. **数据合规**：轨迹与回放数据是否允许落库？是否需要脱敏/加密/分级访问？（决定 trajectory pipeline）

