# Proton（shijianzhong/proton）技术架构与框架方案分析（基于代码细读）

> 范围说明：本文不是 README 复述，而是基于仓库 `src/`、`ui/`、`docs/` 以及关键实现文件的结构化代码走读，给出 **总体架构、运行链路、框架方案（编排/适配/插件/工具/持久化/前端可视化）** 与工程化评估（优缺点、风险、演进建议）。

---

## 1. 项目定位与核心抽象

Proton 的定位是 **“树形 Agent 编排平台（Tree-based Agent Orchestration Platform）”**：通过一个 Workflow（工作流）管理一个 **AgentTree**（树形 Agent 拓扑），每个节点是一个 **AgentNode**，由 **TreeExecutor** 负责递归执行与路由调度，执行结果以 **流式事件（ExecutionEvent）** 形式输出以支持 UI 的实时可视化。

核心抽象（来自 `src/core/models.py` / `src/core/agent_node.py` / `src/core/tree_executor.py`）：

- **AgentType**：  
  - `native`：直接使用 `agent-framework`（Microsoft Agent Framework 的 Python 包）构建的 Agent  
  - `builtin`：平台内置 Agent（可视化编辑 + OpenAI 兼容 function calling + 系统工具）  
  - `coze` / `dify` / `doubao`：第三方平台 Agent  
  - `autogen`：Microsoft AutoGen 生态 Agent  
  - `workflow`：工作流引用（工作流嵌套/复用）  
- **RoutingStrategy**：支持顺序/并行/条件/交接/协调者/意图路由等（特别是 `intent` 策略具有“LLM 选择子节点 + 子查询改写 + 分优先级执行 + 可选结果综合”的完整闭环）。
- **ExecutionContext**：贯穿树执行的上下文，包含消息历史、共享状态、输出缓存、超时预算、错误收集、调用链（防循环、防深度爆炸、跨工作流嵌套检测）与简单的上下文压缩策略。

> 直观理解：Proton 把“Agent 编排”做成了一个可视化、可扩展的框架：  
> **WorkflowManager（多工作流管理） → Workflow（单工作流运行态） → TreeExecutor（递归执行） → AgentAdapter（对接不同 Agent 来源） → Tools/Plugins（对 Agent 提供能力）**。

---

## 2. 技术栈与运行形态

### 2.1 后端（Python / 异步）

从 `requirements.txt` 与实现可确认：

- Web 框架：**FastAPI**（`src/api/main.py`）
- 数据模型：**Pydantic v2**（大量 `BaseModel`）
- 异步 HTTP：**aiohttp**（第三方平台 Adapter、Web 工具等）
- LLM SDK：`openai`（AsyncOpenAI 兼容调用，亦用于“OpenAI-compatible provider”模式）
- Microsoft Agent Framework：`agent-framework>=1.0.0b...`（用于 `native` Agent）
- MCP：`mcp`（通过 stdio / SSE 方式连接 MCP server，并转换为 Tool）
- 持久化：默认 **SQLite（aiosqlite）**，可选 file / postgres（`src/storage/persistence.py`）

### 2.2 前端（Vite + React）

`ui/` 是一个独立前端工程：

- 构建：Vite + TypeScript
- UI：React
- 流程图：ReactFlow（用于 Agent Tree 可视化编辑）
- 网络：Axios
- 状态：Zustand（项目里有依赖）

### 2.3 部署与启动

后端启动方式（README / docs / 实现一致）：

- `python -m src.api.main` 启动 FastAPI（uvicorn）
- 默认使用 `data/proton.db` SQLite（可通过环境变量切换存储类型）

存储选择来自环境变量（与 `config/default.yaml` 的 `storage.type` **并非同一套开关**）：

- `PROTON_STORAGE_TYPE=sqlite|file|postgres`（默认 sqlite）
- `PROTON_STORAGE_PATH=./data`
- `PROTON_SQLITE_PATH=.../proton.db`
- `PROTON_POSTGRES_URL=...`

---

## 3. 代码模块分层（“平台框架方案”）

结合目录结构与调用关系，Proton 的框架分层可以概括为：

1. **API 层（HTTP 控制面）**：`src/api/main.py`  
   - Workflow/Agent CRUD、运行、SSE 事件流  
   - 插件与技能管理  
   - 系统工具查询与内置 Agent 定义编辑  
   - Copilot、Search/Email 配置、发布（publish）、Gateway Router、Super Portal
2. **编排层（Orchestration）**：`src/orchestration/workflow.py`、`src/orchestration/router.py`  
   - WorkflowManager：加载/保存/运行/发布/网关路由  
   - Router：关键词/正则/意图/自定义条件路由（目前 LLM 分类 placeholder）
3. **执行内核（Core Runtime）**：`src/core/tree_executor.py`、`src/core/context.py`、`src/core/agent_node.py`、`src/core/models.py`  
   - TreeExecutor：递归执行、策略路由、事件输出、意图路由与综合  
   - ExecutionContext：调用链、超时、共享 state、上下文压缩、错误收集
4. **适配层（Adapters）**：`src/adapters/*`  
   - 统一 `AgentAdapter` 接口 + `AdapterFactory`（注册表/工厂）  
   - 将不同来源的 Agent（native/builtin/第三方/工作流引用）统一成可执行节点
5. **能力扩展（Plugins / Tools / Skills）**：`src/plugins/*`、`src/tools/*`  
   - PluginRegistry：MCP/Skill/RAG 插件生命周期与“对 Agent 提供工具”  
   - SystemToolRegistry：文件/命令/Web/Email 等系统工具（可按 agent 启用）
6. **高级入口（Copilot / Super Portal）**：`src/copilot/*`、`src/portal/*`  
   - Copilot：自然语言生成/修改工作流（通过 tool calling 驱动后端 CRUD）  
   - Super Portal：把多个已发布 workflow 聚合成一个“智能入口”，带记忆与意图分发
7. **持久化（Storage）**：`src/storage/persistence.py`  
   - 抽象 StorageBackend + SQLite/File/Postgres 实现  
   - StorageManager：workflows/templates/plugins/configs 等集合

---

## 4. 核心运行链路：从 API 到树执行再到事件流

以“运行一个 workflow”为例，典型链路如下：

1. 前端调用 `POST /api/workflows/{workflow_id}/run`（可选 stream）  
2. `WorkflowManager` 找到 `Workflow` 实例  
3. `Workflow.initialize()`：  
   - 获取全局 `PluginRegistry`  
   - 对 tree 中每个 node 调用 `plugin_registry.initialize_for_node(node)`（从 `AgentConfig` 中读取 mcp/skills/rag 配置并注册）  
   - 创建 `TreeExecutor(tree, adapter_factory=create_adapter_for_node)` 并初始化 adapter
4. `TreeExecutor.run(...)`：  
   - 创建/继承 `ExecutionContext`  
   - 从 root 节点开始 `_execute_node` 递归执行  
5. 每个节点执行：  
   - `ExecutionContext.create_child_context()` 做 cycle 检测、深度限制、超时预算分配  
   - `node.adapter.run()` 或 `run_stream()` 执行具体 agent  
6. 根据 `RoutingStrategy` 继续分发子节点：顺序/并行/条件/协调者/意图路由等  
7. 若走 `run_stream_with_events`：会持续产出 `ExecutionEvent`（workflow_start/node_start/node_thinking/node_tool_call/node_complete/routing_start/intent_routing/...）  
8. 前端 ExecutionPanel 消费 SSE/事件流，实时渲染节点状态与输出

> 这套方案的“框架特征”很明确：**执行内核（TreeExecutor）完全与具体 Agent 解耦**，Agent 只是可插拔的 Adapter；而 UI 仅消费事件流，天然适配更多策略/更多节点类型。

---

## 5. TreeExecutor：树形编排、路由策略与“Intent 路由”闭环

### 5.1 执行递归与上下文传递

`TreeExecutor._execute_node()` 的关键点：

- 先检查节点 enabled
- 基于 `ExecutionContext.create_child_context(agent_id)`：  
  - **CycleDetectedError**：调用链重复则报错  
  - **MaxDepthExceededError**：超过最大深度报错  
  - 预算：total_timeout / layer_timeout
- 调用 `node.adapter.run()` 执行，并把输出写回 `context.agent_outputs`
- 若有子节点：根据 routing strategy 决定执行子节点并聚合

`ExecutionContext` 还承担：

- **共享状态** `shared_state`：跨节点共享（工作流引用 input/output mapping 依赖它）
- **上下文压缩**：当估算 token 超过 `max_context_tokens` 时，保留首条与最近 3 轮对话，其他摘要成一条 system message（注意：当前摘要是“截断拼接”，注释写明生产应改用 LLM）
- **错误收集**：`errors` 是共享引用，能汇总全链路错误

### 5.2 路由策略覆盖面

TreeExecutor 内实现（见 `src/core/tree_executor.py`）：

- `sequential`：串行执行子节点，且把每个 child 输出 append 到 context，影响后续 child
- `parallel`：并行 gather 子节点（注意：共享 context 对并行存在竞争风险，见“风险与建议”）
- `conditional/handoff`：基于 `node.routing_conditions` 的简单字符串匹配选择一个 child
- `coordinator`：并行跑 children 后，构建“综合上下文”再调用 parent 做 synthesis
- `intent`：最重要的“LLM 选择子节点 + 子查询改写 + 分优先级执行 + 可选综合”策略

### 5.3 Intent 路由（LLM Router）实现细节

`intent` 的核心由两部分组成：

1. **IntentUnderstandingService（平台级能力）**：`src/portal/intent.py`  
   - 输入：用户 query、可选 history、可选 memory、可用 children 列表  
   - 输出：严格 JSON：`understood_intent` + `dispatch_plans[{workflow_id, sub_query, reason, priority}]`  
   - 失败时 fallback：默认把所有 child 都选上
2. **TreeExecutor._route_intent(...)**：`src/core/tree_executor.py`  
   - `_run_intent_routing()` 调用 IntentUnderstandingService  
   - `_inject_sub_queries()` 将所有子查询写成一条 system message 注入到 context  
   - `_execute_by_priority()`：按 priority 分组，组内并行，组间串行  
   - 可选 synthesise：将 child 输出拼成上下文，再调用当前 node 自己做综合回答（类似 coordinator，但带“意图选择 + 子查询改写”）

> 评价：这是一个相对完整的“意图路由闭环”框架实现，能在任意树层级复用（不仅限于 Portal）。  
> 但目前 `_inject_sub_queries` 是“全 child 共用一条 system message”，并不会为每个 child 精确注入各自 sub_query（见改进建议）。

---

## 6. Adapter 框架方案：用统一接口对接多来源 Agent

### 6.1 统一接口与工厂

`src/adapters/base.py` 定义：

- `AgentAdapter` 抽象接口：`initialize()`、`run()`、`run_stream()`、`get_capabilities()`  
- `AdapterFactory`：基于 `AgentType` 注册并创建 adapter（各 adapter 模块 import 时自注册）

这使得 TreeExecutor 只关心：

```python
node.adapter.run(messages, context)
```

而不关心是 Coze / Dify / 内置 Agent / 子工作流等。

### 6.2 native：直接使用 Microsoft agent-framework

`src/adapters/native.py`：

- 根据 provider 选择 `agent_framework.openai / azure / anthropic / ollama` 的 chat client
- `chat_client.as_agent(name, instructions)` 得到 agent 后直接 `agent.run(af_messages)`
- `ChatMessage` 在 Proton 与 agent-framework 的 Role 之间做转换

### 6.3 builtin：平台内置 Agent（可视化定义 + Function Calling + System Tools）

`src/adapters/builtin.py` 是平台框架的“第二条路线”：不依赖第三方 Agent 平台，而是自己实现一个 OpenAI-compatible 的 tool-calling agent：

关键能力：

- **多 Provider**：通过 AsyncOpenAI 设置 `base_url/api_key`，兼容 openai/azure/anthropic/zhipu/deepseek/qwen/ollama/moonshot/yi/baichuan 等“OpenAI 兼容协议”或半兼容服务
- **工具系统两层**：  
  1) UI 创建的“Built-in tools”（HTTP / code / transform）  
  2) SystemToolRegistry 提供的系统工具（file/shell/web/email…），按 AgentDefinition 的 `system_tools` 选择启用  
- **多轮 tool calling loop**：最多 5 轮，直到模型不再返回 tool_calls
- **工具执行**：  
  - http：aiohttp 调用  
  - code：`exec()` + “简化的 safe_globals”（强调了 WARNING：生产需真正 sandbox）  
  - transform：简单映射/模板渲染  
  - system tools：直接 `await system_tool.execute(**args)`

> 这部分实际上就是“一个轻量的可嵌入 Agent Runtime”，是 Proton 在框架层的核心竞争力之一（可视化定义 + tool calling + 统一执行）。

### 6.4 第三方平台：Coze / Dify / Doubao

这三类 adapter 基本一致：用 aiohttp 调用各自 API，并统一输出为 Proton 的 `AgentResponse` / `AgentResponseUpdate`：

- `coze.py`：调用 `/v3/chat`，SSE 解析 `data:` 行
- `dify.py`：chat/completion/workflow 三模式，SSE 按 Dify 的 event 协议解析
- `doubao.py`：调用 `/api/v3/chat/completions`（Ark OpenAI-like），SSE 解析 choices[].delta.content

### 6.5 autogen：对接 Microsoft AutoGen

`autogen.py`：

- 动态创建 `AssistantAgent` / `UserProxyAgent` / `ConversableAgent` 等
- 实际执行用 `user_proxy.initiate_chat(self._agent, max_turns=1)` 获取结果
- streaming 是“模拟 streaming”（按 chunk 分割输出）

### 6.6 workflow：工作流嵌套（WorkflowAdapter）

`src/adapters/workflow.py`：

- 节点 `type=WORKFLOW` 时，读取 `config.workflow_config.workflow_id`，将执行委派给另一个 workflow
- `ExecutionContext.call_chain.workflow_ids` 做跨工作流循环引用检测
- 支持 input_mapping / output_mapping，把共享状态穿透工作流边界

> 评价：这是“组合式编排”的关键能力，使 Proton 的树不仅能挂 agent，还能挂“子工作流”，从而形成可复用的能力积木。

---

## 7. Plugin 框架方案：MCP / Skill / RAG 统一成 Tool

### 7.1 PluginRegistry：统一生命周期与按 Agent 提供工具

`src/plugins/registry.py`：

- `Plugin` 抽象：`initialize/cleanup/get_tools`
- `Tool` 统一描述：`name/description/parameters_schema/handler/source/metadata`
- `PluginRegistry` 管理：  
  - 注册 mcp/skill/rag plugin  
  - 维护 `agent_id -> plugin_ids` 关联  
  - 供 agent 获取 `get_tools_for_agent(agent_id)`

### 7.2 MCPPlugin：将 MCP server 暴露为工具

`src/plugins/mcp_plugin.py`：

- 支持 `stdio`（mcp.client.stdio）与 `http`（mcp.client.sse）
- `list_tools()` 后将 MCP tool 的 inputSchema 转换为 `Tool.parameters_schema`
- 通过 `call_tool(tool_name, kwargs)` 实际执行

### 7.3 SkillPlugin：将 Python 函数暴露为工具

`src/plugins/skill_plugin.py`：

- 从 module_path + function_name 动态 import
- 使用 type hints + signature 自动生成 JSON schema（也支持 config 直接提供 schema）
- handler 包装同步/异步函数
- 提供 `@skill` decorator（元数据写到函数属性上，但目前 registry 并未自动扫描装载）

### 7.4 RAGPlugin：向量检索工具化

`src/plugins/rag_plugin.py`：

- 支持 `vector_db/file/api`
- vector_db 默认尝试 ChromaDB；也提供 Pinecone/Qdrant placeholder
- 统一暴露 `search_{name}` 工具：输入 query/top_k，输出 results

> 评价：插件框架的“统一 Tool 抽象”是正确方向，但当前内置 Agent（builtin）并没有直接消费 PluginRegistry 的 tools（它主要消费“内置 tool + system tool”），所以插件工具更像是“未来规划的统一 tool surface”，目前与 builtin agent 的 tool calling 仍是两套体系（见建议）。

---

## 8. Skill Package 框架：可安装技能包（SKILL.md + zip）

除了 SkillPlugin（直接注册 Python 函数），Proton 还实现了一套“技能包安装与绑定”机制：

- `src/plugins/skill_parser.py`：解析 `.zip/.skill`，读取 `SKILL.md` 的 YAML frontmatter，解压到 `data/skills/{uuid}/...`
- `src/plugins/skill_manager.py`：维护 `data/skills/registry.json`，支持 install/uninstall/bind/unbind/list/search
- `InstalledSkill` / `SkillPackageMetadata`（`src/core/models.py`）记录版本、入口文件、依赖、是否需要审批等元信息

注意点（代码层面）：

- `SkillManager.get_skill_config()` 会把文件路径映射成 module path：`skills.{skill_id}.{entry}`  
  但仓库内并没有看到自动生成 `skills/` 包结构与 `__init__.py` 的逻辑；如果运行环境缺少相应 python 包路径设置，可能导致 import 失败（需要在平台启动时把 `data/skills` 作为可 import 包路径，或生成包结构）。

---

## 9. System Tools：平台内置“可控工具箱”

`src/tools/registry.py` + `src/tools/*` 提供系统级工具：

- filesystem：读写/追加/列目录/删除（强制限定在 workspace 下，避免越权读写）
- shell：命令执行（含黑名单与危险前缀拦截，且标记 requires_approval=True）
- web：搜索/抓取/下载（并提供 SearchConfig 的数据库持久化）
- email：发送邮件（同样支持 EmailConfig 数据库持久化）

> 重要但容易忽略：工具类中声明了 `requires_approval` / `is_dangerous`，但在当前代码里 **没有看到统一的“审批执行器”在后端强制拦截**。  
> 也就是说，这些字段更多是“用于 UI 展示/未来策略”，而不是强安全边界（见风险章节）。

---

## 10. Copilot：自然语言生成/修改工作流（Workflow Copilot）

`src/copilot/service.py` 实现一个“工作流生成助手”：

- 多 provider（OpenAI-compatible）  
- tool calling：通过 `CopilotTools`（未在本文全文展开）调用后端能力实现“generate_workflow / patch_workflow / get_workflow_summary”
- 配置持久化：copilot config 存在 storage 的 `configs` 集合（SQLite items 表）
- API 层提供：  
  - `/api/copilot/sessions`、`/api/copilot/chat`  
  - `/api/copilot/config`（读写配置）  

这使 Proton 不仅是“执行框架”，还是“创建工作流的生产力工具”。

---

## 11. Super Portal：工作流聚合入口（带记忆与意图分发）

Portal 子系统位于 `src/portal/*`，相当于把多个已发布 workflow 包装成一个“超级入口”：

### 11.1 IntentUnderstandingService：可复用的 LLM Router

同一份 intent 能力被两处复用：

- Portal：从多个 workflow 里选要执行的 workflow
- TreeExecutor（routing_strategy=intent）：从子 agent 里选要执行的子 agent

### 11.2 PortalMemoryManager：轻量长期记忆

- 存储：使用 StorageBackend 的 `portal_memories` collection
- 检索：关键词 overlap * importance * recency 的打分
- 自动记忆抽取：`extract_and_store()` 使用 LLM 从对话 turn 抽取“值得记住的事实/偏好”

### 11.3 PortalService：一次 turn 的完整闭环

`chat()` 的 pipeline 很清晰：

1. load/create session（PortalSession）
2. retrieve memories
3. intent routing（得到 dispatch plans）
4. 分优先级并行/串行执行 workflows
5. synthesis 综合答案（并支持 streaming）
6. 异步抽取新记忆
7. persist session

API 层已提供 portal CRUD、session、chat SSE、memory 查询/删除等接口。

> 评价：Portal 把 Proton 从“编排执行框架”进一步推向“可产品化入口”（一个 endpoint 聚合多个能力工作流），并且将“意图理解 + 记忆 + 调度 + 综合”固化为标准流程。

---

## 12. 存储与配置持久化：SQLite / File / Postgres 三后端

`src/storage/persistence.py` 的核心设计：

- `StorageBackend` 抽象：`initialize/save/load/delete/list_all/close`
- FileStorageBackend：每个 item 一个 json 文件（开发友好）
- SQLiteStorageBackend：单表 `items(collection,id,data,created_at,updated_at)`（通用键值存储）
- PostgresStorageBackend：同样的 items 表结构（JSONB）
- `StorageManager` 封装 collection 名称与高层 CRUD：workflows/templates/plugins/configs/…
- 全局单例 `get_storage_manager()`：默认 sqlite，使用 env 控制类型

配置持久化（见 `docs/CONFIG_PERSISTENCE.md`）已覆盖：

- EmailConfig（邮件）
- SearchConfig（搜索）
- CopilotService（模型配置）

> 注意：`config/default.yaml` 中的 `storage.type: "memory"` 看起来是“早期设计/文档遗留”，实际运行默认走 sqlite（除非 env 指定 file/postgres）。

---

## 13. 前端 UI：可视化编排 + 执行监控 + Portal Chat

### 13.1 WorkflowEditor（ReactFlow）

- 将 workflow tree.nodes 映射为 ReactFlow nodes/edges
- 保存策略：对比后端现有 agents 列表，把新增 node 通过 API 创建（属于“增量同步”）
- 双击节点打开 AgentEditor（编辑 builtin_definition、tools 等）
- ExecutionPanel：通过 SSE 事件流实时渲染执行过程

### 13.2 ExecutionPanel：消费 ExecutionEvent

ExecutionPanel 维护：

- workflow_status（running/completed/error）
- nodes Map（每个 node 的 thinking 内容、tool calls、tool results、duration）

并根据 event_type 更新 UI。

代码层面可见两个值得注意的实现问题：

1. `ExecutionEventType` 的前端 union 类型未包含后端新增的 `intent_routing`（后端在 TreeExecutor events-path 会 emit），因此 UI 无法可视化该事件。  
2. `ExecutionPanel.tsx` 里 `case 'node_tool_result'` 分支重复出现两次，属于明显的逻辑重复/潜在 bug。

### 13.3 PortalList / PortalChat

App 的 menu 已包含“超级入口”，PortalChat 会走 portal chat SSE（PortalEvent）实现多工作流聚合聊天体验。

---

## 14. 框架方案评价：它本质上是“树形编排内核 + 多源 Agent 适配 + 内置 Agent Runtime + 事件可视化”的组合

从代码实现看，Proton 的框架方案可以总结成 4 个层级的“能力栈”：

1. **编排内核（TreeExecutor + ExecutionContext）**：完成树执行、策略路由、上下文治理（深度/循环/超时/压缩）、事件流输出  
2. **统一适配层（AdapterFactory + adapters）**：将外部平台/框架/native agent/子工作流统一成可执行节点  
3. **内置 Agent Runtime（BuiltinAgentAdapter + SystemTools）**：在平台内部实现一个兼容 OpenAI function calling 的可编辑 agent，引入“工具系统”作为主要扩展手段  
4. **产品化入口（Copilot / Portal）**：把“创建工作流”和“多工作流聚合对话”封装为可用产品能力

这套方案适合的场景：

- 需要快速搭建“多专家协作/多子任务分解”的 Agent 工作流
- 同时要对接第三方平台（Coze/Dify/豆包）与自研 Agent（native/builtin/autogen）
- 需要可视化编辑与执行可观测（事件流+UI）
- 希望把 workflow 发布成 API，并进一步聚合成“超级入口”

---

## 15. 风险点与改进建议（基于代码真实行为）

下面是从代码细读能明确指出的工程风险与改造方向（并非抽象建议）。

### 15.1 安全：工具审批并未形成后端强约束

- system tool 标记了 `requires_approval/is_dangerous`，但 BuiltinAgentAdapter 里执行 system tool 时 **直接 execute**，并没有统一的 approval gate。  
- Shell 工具虽然有危险命令拦截，但仍然属于“弱安全”（黑名单/前缀），并且没有用户确认机制。

建议：

1. 在后端引入“Tool Execution Policy Engine”（即便先做简单 allowlist + approval-required 阻断），并将审批事件纳入 ExecutionEvent。  
2. Builtin tool 的 `code` 执行目前是 `exec()` + 简化 builtins，依然可被绕过（且注释已明确 WARNING）。建议用容器/沙箱（如 Pyodide、firejail、gVisor、Docker、nsjail）替换。

### 15.2 并行执行与共享 context 的竞态风险

TreeExecutor 的 parallel/intent priority group 并行，会让多个 child 共享同一个 ExecutionContext（其中 shared_state/errors/agent_outputs 是共享引用，messages 在 child_context 是 copy，但并行仍可能写入共享结构）。

建议：

- 并行分支使用“上下文快照 + 合并策略”：  
  - 每个并行 child 拿到独立的 child_context（深拷贝 shared_state 或使用事务式合并）  
  - 并行结束后再合并到父 context（冲突策略：last-write-wins 或按 key namespace）

### 15.3 插件 Tool 体系与 Builtin Agent 的 Tool 体系割裂

PluginRegistry 能把 MCP/Skill/RAG 统一成 Tool，但 BuiltinAgentAdapter 的 tool calling 只认：

- UI 内置的 BuiltinToolDefinition
- SystemToolRegistry 的 tools

这会导致：

- 你为某个 agent 配置了 MCP/Skill/RAG 插件，但 builtin agent 可能“看不见”这些工具（除非另有桥接逻辑）。
- Tool 生态分裂：一部分工具走 plugins，一部分工具走 builtin/system。

建议（两条路线二选一，或并行推进）：

1. **统一 Tool Surface**（推荐）：让 BuiltinAgentAdapter 的 `available_tools` 同时包含：  
   - 内置 tools  
   - system tools  
   - `plugin_registry.get_tools_for_agent(agent_id)`（把插件工具也转成 OpenAI schema）  
   并统一走同一个 tool 执行器（policy/approval/sandbox 也可统一）。  
2. **明确“builtin 仅支持 system/builtin tools”**：如果短期不打算统一，则需要在 UI/配置层做强约束与提示，避免用户误以为配置了插件就能被 builtin agent 自动调用。

### 15.4 配置体系存在“双轨”：YAML 默认值 vs 环境变量/数据库

代码里可以看到三套配置来源：

1. `config/default.yaml`（看起来是平台配置模板）
2. 环境变量（决定存储类型、路径、数据库连接等）
3. SQLite `configs` collection（email/search/copilot 等配置）

其中最明显的偏差是：YAML 中 `storage.type: memory`，但实际默认 `initialize_storage()` 是 sqlite（除非 env 覆盖）。

建议：

- 给配置来源定义明确优先级（env > db > yaml 或者 env > yaml > db 等），并在启动日志中打印“最终生效配置快照”。  
- 对存储后端这种“基础设施配置”，建议统一为 env/yaml，不要混入 db（否则迁移与调试成本高）。  
- 对用户侧偏好/凭证/运行参数（email/search/copilot），走 db 合理，但需配套加密与权限。

### 15.5 事件协议与前端类型不同步

后端在 intent routing 逻辑里可能 emit `intent_routing` 事件（`ExecutionEventType.INTENT_ROUTING`），但前端 `ExecutionEventType` union 未包含该类型；此外 ExecutionPanel 存在重复 case。

建议：

- 把事件协议做成共享 schema（OpenAPI/JSON schema/ts 类型生成），避免后端加事件、前端丢可视化。  
- ExecutionEvent 作为“平台核心接口”，建议版本化（event_version）并提供兼容层。

### 15.6 Skill Package 的 Python import 路径需要工程化处理

SkillManager 把解压后的 skill entry 映射成 `skills.{skill_id}.{entry}` 的 module path，但当前仓库未看到：

- 自动生成 `__init__.py`（形成包）
- 或把 skill 解压目录挂载进 `sys.path` 的统一入口

建议：

- 明确 skill 运行时机制：  
  - “作为 Python 包导入”（生成包结构 + 加入 sys.path）  
  - 或“作为脚本执行”（更像工具/动作，避免 import 的包语义）  
- 若要做生态化分发（版本/依赖/安全扫描），更推荐“脚本执行 + 受控 API”而不是任意 import。

### 15.7 Builtin code tool 的安全边界与可观测性不足

当前 code tool：

- 通过 `exec()` 直接运行，虽然减少了 builtins，但仍可能被绕过  
- 输出以 stdout 捕获为主；没有资源限制（CPU/内存/文件/网络）

建议：

- 将 code tool 纳入 Execution Backend：  
  - 本地/容器/远端后端统一执行  
  - 资源限制 + 超时 + 网络隔离  
  - 产出结构化日志（stderr/stdout/exit_code/artifacts）

---

## 16. 总结：Proton 的“架构亮点”与“下一步最值钱的工程化补齐”

### 16.1 架构亮点（已经做对的部分）

1. **TreeExecutor 把编排与执行内核做成了可复用框架**：策略丰富、可扩展，且天然支持可视化与事件流。  
2. **AdapterFactory + 多来源 Agent 适配**：让系统既能接外部平台，又能自研内置 agent runtime，并支持工作流复用。  
3. **Portal 与 Copilot 把“使用”与“创建”两端产品化**：一个负责“聚合入口”，一个负责“生成编排”，很容易形成闭环。  
4. **存储后端抽象与 SQLite 默认**：易于开箱即用，也具备向 Postgres 迁移的路径。

### 16.2 最优先补齐（若目标是更接近生产级平台）

按“投入产出比”排序的 6 个动作：

1. **统一 Tool 执行与策略/审批**（形成后端强约束）  
2. **并行执行的上下文隔离与合并机制**（消除竞态/提升可预测性）  
3. **事件协议类型化与版本化**（后端/前端一致演进）  
4. **技能/插件生态的安全与运行时标准化**（包结构、依赖、审计、签名）  
5. **配置体系收敛**（明确优先级、输出生效快照、减少双轨）  
6. **code 工具沙箱化**（把“能跑”升级为“可控地跑”）

---

## 附：建议的“阅读顺序”（给想进一步深入代码的人）

1. `src/core/models.py`：所有核心数据结构（AgentNode/Workflow/ExecutionEvent/Config）  
2. `src/core/tree_executor.py`：树执行、路由策略、intent routing、事件流  
3. `src/orchestration/workflow.py`：Workflow/WorkflowManager 的生命周期与持久化  
4. `src/adapters/*`：多来源 agent 接入与统一接口  
5. `src/tools/*` + `src/plugins/*`：工具与插件体系  
6. `src/portal/*` 与 `src/copilot/*`：产品化入口能力  
7. `ui/src/components/WorkflowEditor.tsx` 与 `ExecutionPanel.tsx`：前端编排与执行可视化

