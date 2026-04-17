# Proton Agent Platform - 项目文档

## 项目概述

Proton 是一个基于 Microsoft Agent Framework 构建的**树形 Agent 编排平台**，用于构建多 Agent 协作系统。

### 核心特性

- **树形 Agent 架构**: 支持主 Agent 下挂多层子 Agent，形成层级结构
- **多平台集成**: 支持 Native、Coze、Dify、豆包、AutoGen 等多种 Agent 类型
- **插件系统**: 支持 MCP (Model Context Protocol)、Skill、RAG 等插件挂载
- **智能路由**: 支持顺序、并行、条件、交接、协调者等多种路由策略
- **深层嵌套保护**: 循环检测、深度限制、上下文压缩
- **REST API**: 完整的 FastAPI 服务支持
- **可视化编排**: Web UI 支持 Agent 关系编排（开发中）

---

## 项目结构

```
proton/
├── src/
│   ├── core/               # 核心模块
│   │   ├── models.py       # 数据模型定义 (Pydantic)
│   │   ├── agent_node.py   # Agent 节点和树结构
│   │   ├── context.py      # 执行上下文管理 (含 MemPalace offloader)
│   │   └── tree_executor.py # 树形执行引擎
│   │
│   ├── adapters/           # Agent 适配器层
│   │   ├── base.py         # 适配器基类和工厂
│   │   ├── native.py       # 原生 Agent (OpenAI/Azure/Anthropic/Ollama)
│   │   ├── builtin.py      # 内置可视化 Agent (多 LLM 提供商 + 多轮工具调用)
│   │   ├── coze.py         # Coze 平台适配器
│   │   ├── dify.py         # Dify 平台适配器
│   │   ├── doubao.py       # 豆包平台适配器
│   │   ├── autogen.py      # AutoGen 框架适配器
│   │   └── workflow.py     # 工作流间调用适配器
│   │
│   ├── plugins/            # 插件系统
│   │   ├── registry.py     # 插件注册中心
│   │   ├── mcp_plugin.py   # MCP 协议插件
│   │   ├── mcp_manager.py  # MCP 服务器生命周期管理
│   │   ├── skill_plugin.py # Skill 技能插件
│   │   ├── skill_manager.py# Skill 管理
│   │   ├── skill_parser.py # Skill 代码解析
│   │   └── rag_plugin.py   # RAG 检索增强插件
│   │
│   ├── orchestration/      # 编排引擎
│   │   ├── router.py       # 路由策略实现
│   │   ├── aggregator.py   # 结果聚合器
│   │   └── workflow.py     # 工作流管理 (含发布/API 密钥)
│   │
│   ├── execution/          # 执行沙箱
│   │   ├── tool_executor.py# 统一工具执行器 (含治理切片)
│   │   ├── tool_provider.py# 工具提供商注册
│   │   └── backends/
│   │       ├── base.py     # 执行后端基类
│   │       ├── local.py    # 本地进程后端 (含路径安全)
│   │       └── docker.py   # Docker 容器后端 (含降级)
│   │
│   ├── governance/         # 治理与安全
│   │   ├── approval.py     # 人机协同审批系统
│   │   ├── policy_engine.py# 策略引擎
│   │   └── tool_governance.py # 工具治理
│   │
│   ├── tools/              # 内置工具
│   │   ├── base.py         # 工具基类
│   │   ├── registry.py     # 工具注册
│   │   ├── web.py          # 网页搜索工具
│   │   ├── email.py        # 邮件发送工具
│   │   ├── filesystem.py   # 文件系统工具
│   │   └── shell.py        # Shell 命令工具
│   │
│   ├── portal/             # Super Portal 超级入口
│   │   ├── service.py      # Portal 运行时 (对话生命周期)
│   │   ├── intent.py       # 意图理解服务 (LLM 路由)
│   │   ├── safety.py       # 生成前安全扫描
│   │   ├── trajectory.py   # 执行轨迹提取
│   │   ├── memory_provider.py    # 记忆提供商接口
│   │   ├── mempalace_memory_provider.py # MemPalace 记忆实现
│   │   └── mempalace_client.py   # MemPalace MCP 客户端
│   │
│   ├── copilot/            # AI Copilot 辅助
│   │   ├── service.py      # Copilot 服务
│   │   ├── schema.py       # Copilot 数据模型
│   │   ├── session_manager.py # 会话管理
│   │   ├── tools.py        # Copilot 工具
│   │   └── prompts.py      # 提示词模板
│   │
│   ├── artifacts/          # 产物工厂 (Learning Loop)
│   │   └── service.py      # 轨迹→产物自动提取
│   │
│   ├── integrations/       # 多渠道集成
│   │   ├── models.py       # 渠道数据模型
│   │   ├── store.py        # 渠道配置存储
│   │   ├── gateway.py      # 渠道网关
│   │   ├── runtime.py      # 渠道运行时
│   │   ├── tls.py          # TLS 配置
│   │   ├── ssl_bootstrap.py# SSL 引导
│   │   └── connectors/
│   │       ├── base.py     # 连接器基类
│   │       ├── feishu.py   # 飞书连接器 (WebSocket + Webhook)
│   │       ├── dingtalk.py # 钉钉连接器 (Stream)
│   │       ├── telegram.py # Telegram 连接器
│   │       ├── weixin.py   # 微信连接器 (扫码登录)
│   │       └── weixin_media.py # 微信媒体处理
│   │
│   ├── storage/            # 持久化存储
│   │   └── persistence.py  # SQLite 持久层 (aiosqlite)
│   │
│   └── api/                # REST API
│       └── main.py         # FastAPI 应用入口 (100+ 端点)
│
├── config/
│   └── default.yaml        # 默认配置文件
│
├── ui/                     # Web UI (Vite + TypeScript + ReactFlow)
│   └── src/
│       ├── App.tsx                     # 主应用 (5 个页面)
│       ├── api/client.ts               # API 客户端 (Axios)
│       └── components/
│           ├── WorkflowEditor.tsx      # 工作流可视化编辑器 (ReactFlow)
│           ├── WorkflowList.tsx        # 工作流列表 + 模板
│           ├── PortalList.tsx          # 超级入口列表 + 管理
│           ├── PortalChat.tsx          # 超级入口对话 (SSE + 事件卡片)
│           ├── RootPortalChat.tsx      # Root 超级入口对话
│           ├── AgentEditor.tsx         # Agent 详细编辑器
│           ├── AgentNode.tsx           # ReactFlow Agent 节点
│           ├── SettingsPanel.tsx       # 系统设置 (搜索/邮件/Copilot)
│           ├── ExecutionPanel.tsx      # 执行面板 (事件流可视化)
│           ├── CopilotPanel.tsx        # AI Copilot 面板
│           ├── SkillMarket.tsx         # 技能市场
│           ├── ToastProvider.tsx       # Toast 通知
│           └── executionState.ts       # 执行状态类型
│
├── proton_global_memory/   # MemPalace 记忆存储目录
├── docs/
│   └── TECHNICAL_DESIGN.md # 技术设计文档
│
└── venv/                   # Python 虚拟环境
```

---

## 核心架构

### 1. 数据模型 (`src/core/models.py`)

#### Agent 类型 (`AgentType`)
```python
NATIVE = "native"      # 原生 agent-framework Agent
BUILTIN = "builtin"    # 内置可视化编辑 Agent
COZE = "coze"          # Coze 平台
DIFY = "dify"          # Dify 平台
DOUBAO = "doubao"      # 豆包平台
AUTOGEN = "autogen"    # AutoGen 框架
WORKFLOW = "workflow"  # 工作流间调用 (无限深度嵌套)
CUSTOM = "custom"      # 自定义适配器
```

#### 路由策略 (`RoutingStrategy`)
```python
SEQUENTIAL = "sequential"       # 顺序执行子 Agent
PARALLEL = "parallel"           # 并行执行所有子 Agent
CONDITIONAL = "conditional"     # 根据条件路由到特定子 Agent
HANDOFF = "handoff"             # 专家交接模式
HIERARCHICAL = "hierarchical"   # 任务分解模式
COORDINATOR = "coordinator"     # 协调者模式：父→子→父整合
ROUND_ROBIN = "round_robin"     # 轮询分发
LOAD_BALANCED = "load_balanced" # 负载均衡
INTENT = "intent"               # LLM 驱动的动态子节点选择
```

#### 关键模型
- `ChatMessage`: 对话消息
- `AgentResponse`: Agent 响应
- `AgentConfig`: Agent 配置（含各平台配置）
- `WorkflowConfig`: 工作流配置
- `ExecutionEvent`: 执行事件（用于实时可视化）

### 2. Agent 节点 (`src/core/agent_node.py`)

#### `AgentNode` - 树形结构节点
```python
@dataclass
class AgentNode:
    id: str                          # 唯一标识
    name: str                        # 显示名称
    type: AgentType                  # Agent 类型
    config: AgentConfig              # 配置信息
    parent_id: Optional[str]         # 父节点 ID
    children: List[str]              # 子节点 ID 列表
    routing_strategy: RoutingStrategy # 子 Agent 调用策略
    routing_conditions: Dict[str, str] # 条件路由规则
    plugins: List[PluginConfig]      # 挂载的插件
    max_depth: int                   # 最大递归深度
    timeout: float                   # 执行超时
```

#### `AgentTree` - 树结构管理
- `add_node()` / `remove_node()`: 节点增删
- `get_children()` / `get_parent()`: 遍历
- `get_ancestors()` / `get_descendants()`: 祖先/后代查询
- `validate()`: 结构验证（检测孤立节点、无效引用）

### 3. 执行上下文 (`src/core/context.py`)

#### `CallChain` - 调用链追踪
```python
@dataclass
class CallChain:
    chain: List[str]       # 调用路径 [root_id, child_id, ...]
    depth: int             # 当前深度
    start_time: float      # 开始时间
    context_tokens: int    # 上下文 token 估算

    def check_cycle(agent_id) -> bool    # 循环检测
    def check_depth(max_depth) -> bool   # 深度检测
```

#### `ExecutionContext` - 执行上下文
- 上下文传递与压缩
- 超时管理
- 错误追踪
- Agent 输出存储

#### 自定义异常
- `CycleDetectedError`: 检测到循环调用
- `MaxDepthExceededError`: 超过最大深度
- `AgentExecutionError`: Agent 执行错误
- `WorkflowExecutionError`: 工作流执行错误

### 4. 树形执行器 (`src/core/tree_executor.py`)

#### `TreeExecutor` - 核心编排引擎
```python
class TreeExecutor:
    async def run(input_message, context) -> AgentResponse
    async def run_stream(input_message) -> AsyncIterator[AgentResponseUpdate]
    async def run_stream_with_events(...) -> AsyncIterator[ExecutionEvent]
```

执行流程:
1. 创建子上下文 (检查循环/深度)
2. 调用当前 Agent
3. 根据路由策略分发到子 Agent
4. 聚合结果返回

#### 路由策略实现
- `_route_sequential()`: 顺序执行，上下文传递
- `_route_parallel()`: 并行执行 `asyncio.gather()`
- `_route_conditional()`: 条件匹配路由
- `_route_coordinator()`: 协调者模式（子执行后父再次整合）

### 路由策略详细说明与使用场景

#### 1. Sequential (顺序执行)
```
Parent → Child1 → Child2 → Child3 → 结果
```
- **工作方式**: 子 Agent 按顺序逐个执行，前一个的输出会加入到后一个的上下文中
- **适用场景**:
  - 流水线处理（翻译→润色→校对）
  - 步骤依赖任务（分析→总结→格式化）
  - 信息逐步丰富

#### 2. Parallel (并行执行)
```
Parent → [Child1, Child2, Child3] → 聚合结果
```
- **工作方式**: 所有子 Agent 同时执行，执行完成后收集所有结果
- **适用场景**:
  - 独立子任务（多语言翻译）
  - 多角度分析（技术分析 + 市场分析 + 风险分析）
  - 提高效率的批量处理

#### 3. Conditional (条件路由)
```
Parent → [根据条件选择] → Child1 或 Child2 或 Child3
```
- **工作方式**: 根据父 Agent 输出内容匹配 `routing_conditions`，选择执行特定子 Agent
- **配置方式**: 在 `routing_conditions` 中设置 `"keyword == '技术'": "tech_agent_id"`
- **适用场景**:
  - 意图分类路由
  - 根据输入类型分发到专家

#### 4. Handoff (交接模式)
```
Parent → 选择专家 → Specialist Agent
```
- **工作方式**: 类似 Conditional，但更强调能力委托
- **适用场景**:
  - 复杂问题委托给专家
  - 多轮对话中的专家切换

#### 5. Coordinator (协调者模式)
```
Parent → [Child1, Child2] → Parent 整合 → 最终结果
```
- **工作方式**: 父 Agent 先发送任务给子 Agent，子 Agent 执行后返回，父 Agent 再次处理整合所有结果
- **适用场景**:
  - 多专家协作
  - 需要综合多方意见
  - 共识构建

#### 6. Hierarchical (层级分解)
```
Parent [分解任务] → [子任务1, 子任务2] → [聚合结果]
```
- **工作方式**: 父 Agent 将复杂任务分解为子任务，分发给子 Agent，最后聚合
- **适用场景**:
  - 复杂任务分解
  - 分治策略

### 在 UI 中配置路由策略

1. **创建 Agent 关系**: 在 Workflow Editor 中用连线将父 Agent 连接到子 Agent
2. **设置路由策略**: 双击父 Agent → Settings 标签 → 选择 Routing Mode
3. **保存配置**: 点击 Save 按钮

### 通过 API 配置路由策略

```python
# 创建带路由策略的 Agent
response = requests.post(
    f"http://localhost:8000/api/workflows/{workflow_id}/agents",
    json={
        "name": "Router Agent",
        "type": "builtin",
        "routing_strategy": "conditional",  # 设置路由策略
        "parent_id": parent_agent_id,
    }
)
```

---

## 适配器系统 (`src/adapters/`)

### 基类 `AgentAdapter`
```python
class AgentAdapter(ABC):
    async def initialize() -> None
    async def run(messages, context) -> AgentResponse
    async def run_stream(messages, context) -> AsyncIterator[AgentResponseUpdate]
    def get_capabilities() -> AgentCapabilities
    async def cleanup() -> None
```

### `AdapterFactory` - 适配器工厂
```python
AdapterFactory.register(AgentType.COZE, CozeAgentAdapter)
adapter = AdapterFactory.create(node)
```

### 已实现适配器

| 适配器 | 文件 | 说明 |
|--------|------|------|
| NativeAgentAdapter | `native.py` | OpenAI/Azure/Anthropic/Ollama |
| CozeAgentAdapter | `coze.py` | Coze 平台 (ByteDance) |
| DifyAgentAdapter | `dify.py` | Dify 平台 (chat/completion/workflow 模式) |
| DoubaoAgentAdapter | `doubao.py` | 豆包平台 |
| AutoGenAgentAdapter | `autogen.py` | AutoGen 框架 |

---

## 插件系统 (`src/plugins/`)

### `PluginRegistry` - 插件注册中心
```python
registry = get_plugin_registry()
await registry.register_mcp(mcp_config, agent_id)
await registry.register_skill(skill_config, agent_id)
await registry.register_rag(rag_config, agent_id)

tools = registry.get_tools_for_agent(agent_id)
```

### 插件类型

#### 1. MCP Plugin (`mcp_plugin.py`)
- 支持 stdio 和 HTTP 传输
- 自动发现 MCP 服务器工具
- 工具调用代理

#### 2. Skill Plugin (`skill_plugin.py`)
- Python 函数注册为工具
- 支持审批流程

#### 3. RAG Plugin (`rag_plugin.py`)
- 支持向量数据库: ChromaDB, Pinecone, Qdrant
- 支持文件源和 API 源
- 语义搜索工具

---

## 编排引擎 (`src/orchestration/`)

### Router (`router.py`)
路由条件类型:
- `KEYWORD`: 关键词匹配
- `REGEX`: 正则表达式
- `INTENT`: 意图分类
- `CUSTOM`: 自定义函数

### Aggregator (`aggregator.py`)
聚合策略:
- `CONCAT`: 拼接所有响应
- `MERGE`: 合并为单一响应
- `VOTE`: 投票选择
- `BEST`: 选择最佳响应
- `SUMMARIZE`: LLM 总结

### Workflow (`workflow.py`)
```python
manager = get_workflow_manager()
workflow = await manager.create_workflow("My Workflow", "Description", root_agent)
workflow.add_agent(child_agent, parent_id)
await workflow.initialize()
result = await workflow.run("Hello!")
```

工作流还支持:
- 发布为 API 服务 (`publish_workflow` / `unpublish_workflow`)
- 通过 API 密钥执行 (`run_workflow_stream_events`)
- 工作流间嵌套调用 (`bind-workflow` 端点)
- 工作流模板 (`create_workflow_from_template`)

---

## 执行沙箱 (`src/execution/`)

### ToolExecutor (`tool_executor.py`)
统一工具执行器，支持治理切片 (Slice) 拦截:
```python
@dataclass
class ExecutableTool:
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    handler: ToolHandler
    source: str = "custom"
    approval_required: bool = False
    is_dangerous: bool = False
```

### 执行后端 (`backends/`)
- **LocalProcessBackend**: 本地进程执行 (带工作区路径安全校验，防止目录遍历)
- **DockerBackend**: Docker 容器隔离执行 (含降级到 LocalProcessBackend)

### 工具提供商 (`tool_provider.py`)
- **SystemToolProvider**: 内置系统工具 (web/email/filesystem/shell)
- **PluginToolProvider**: 插件工具 (MCP/Skill/RAG)
- **BuiltinToolProvider**: UI 定义的自定义工具

---

## 治理与安全 (`src/governance/`)

### 审批系统 (`approval.py`)
- 人机协同审批: `ApprovalStatus` (PENDING/APPROVED/DENIED)
- 数据库持久化 + 原子性 compare-and-set 解决并发审批
- 确定性审批 ID (SHA256 哈希 `tool_call_id`)

### 策略引擎 (`policy_engine.py`)
- 规则优先级链: dm_policy 配对 → deny_tools → allow_tools → 路径/URL 模式 → require_approval
- 使用 `fnmatch` 模式匹配
- 策略来源: 执行上下文元数据 + shared_state

### 工具治理切片 (`tool_governance.py`)
- 实现 `ToolExecutionSlice` 协议，拦截 `before_execute()`
- 检查策略引擎结果 + 审批状态
- 支持运行时覆盖 (`approved_tools` / `approved_tool_calls`)
- 自动记录审计轨迹到执行上下文

---

## 内置工具 (`src/tools/`)

### 工具基类 (`base.py`)
- `SystemTool(ABC)`: 定义 `name`, `description`, `parameters`, `execute()`
- 提供 `to_openai_schema()` 用于 LLM function calling

### 工具注册 (`registry.py`)
- 单例 `SystemToolRegistry`，自动加载所有内置工具

### 网页工具 (`web.py`)
- **WebSearchTool**: 多搜索引擎支持 (Bing/SearXNG/Serper/Brave/Tavily/Google/DuckDuckGo)
- **WebFetchTool**: 提取网页文本内容
- **WebDownloadTool**: 下载文件
- Bing 支持 API 和网页爬取降级; Tavily 使用 "advanced" 搜索深度

### 邮件工具 (`email.py`)
- **SendEmailTool**: 支持 Resend API 和 SMTP 双通道
- **CheckEmailConfigTool**: 检查邮件配置状态
- 运行时配置保存到数据库; 自动检测优选方式 (Resend > SMTP)

### 文件系统工具 (`filesystem.py`)
- **FileReadTool** / **FileWriteTool** / **FileAppendTool** / **FileListTool** / **FileDeleteTool**
- 工作区路径安全校验 (`resolve().relative_to()`)
- 写/追加/删除操作需审批

### Shell 工具 (`shell.py`)
- **ShellExecTool**: 同步命令执行
- **ShellExecBackgroundTool**: 后台进程执行
- 阻止危险命令 (rm -rf /, fork bombs 等)
- 最大超时 300s; 在工作区目录下执行

---

## Super Portal 超级入口 (`src/portal/`)

### Portal 服务 (`service.py`)
完整的超级入口对话生命周期:
1. 加载会话 → 2. 检索记忆 → 3. 子 Portal 路由 → 4. 意图理解选择工作流
5. 按优先级执行工作流 → 6. 生成前安全扫描 → 7. 综合最终答案
8. 后台提取记忆和轨迹

### 意图理解 (`intent.py`)
- 平台级意图理解服务，用于 Portal 和工作流节点路由
- LLM 驱动的动态子节点选择，支持多子节点并行/优先级执行
- 包含: 记忆快照、长期记忆、会话检索、历史对话上下文

### 安全扫描 (`safety.py`)
- 生成前安全扫描器，使用正则匹配检测高风险合成上下文
- 检测规则: prompt injection, secret exfiltration, 危险命令, 策略绕过
- 严重程度分级 (none/low/medium/high); >= high 时阻断

### 轨迹提取 (`trajectory.py`)
- 信号累积器，支持 L1→L2→L3 记忆沉淀
- 线程安全池，大小/时间阈值 (默认: 20 条或 1 小时)
- 检测显式保存/记住关键词触发 L3 沉淀

### 记忆系统 (`memory_provider.py` + `mempalace_memory_provider.py`)
- **PortalMemoryProvider**: 记忆提供商接口 (`retrieve()`, `bounded_snapshot()`, `write_turn()`)
- **MemPalaceMemoryProvider**: 基于 MemPalace MCP 的实现
  - Wing 分区策略: per_user / per_portal / shared
  - 支持跨 Portal 全局记忆

### MemPalace 客户端 (`mempalace_client.py`)
- MCP 客户端封装，连接 MemPalace MCP server
- 重试逻辑 + 指数退避; 不健康冷却期
- 静态方法 `build_wing()` 和 `build_room()` 用于一致命名

---

## AI Copilot 辅助 (`src/copilot/`)

### Copilot 服务 (`service.py`)
- 自然语言工作流生成，通过多轮 LLM 对话
- 支持 10+ LLM 提供商 (OpenAI/Azure/Anthropic/Zhipu/DeepSeek/Qwen/Ollama/Moonshot/Yi/Baichuan)
- 配置优先级: 构造函数参数 > 环境变量 > 配置文件 > 数据库存储

### 工作流生成工具
- `generate_workflow()`: 根据描述生成工作流计划
- `patch_workflow()`: 修改现有工作流
- `get_workflow_summary()`: 获取工作流摘要

### 会话管理 (`session_manager.py`)
- 创建/加载会话，支持持久化存储
- 跟踪消息历史和关联的工作流 ID

---

## 产物工厂 (Learning Loop) (`src/artifacts/`)

### ArtifactFactory (`service.py`)
- **L1 学习循环**: 基于执行轨迹的自动产物提取
- **L2 学习循环**: 信号积累触发产物生成
- **L3 学习循环**: 显式保存触发即时产物生成
- 启发式评分: parallel branches × tool counts × long-running flags
- 灰度发布管理: GRAYSCALE → FULL_RELEASED / ROLLED_BACK
- A/B 路由: 基于哈希的桶分配，可配置控制比例
- 指标跟踪: 成功率、错误率、延迟 P95、质量评分
- 轨迹聚类: Jaccard 相似度发现重复模式
- 自动修订: 监控指标降级并触发修订候选

---

## 多渠道集成 (`src/integrations/`)

### 连接器 (`connectors/`)
- **Feishu (飞书)**: WebSocket + Webhook 模式，支持挑战验证和卡片交互
- **DingTalk (钉钉)**: Stream 模式，支持消息回调和交互式卡片
- **Telegram**: Polling 或 Webhook 模式，支持内联键盘审批
- **WeChat (微信)**: 扫码登录 + 公众号消息处理

### 渠道网关 (`gateway.py`)
- 统一消息网关，将社交渠道事件路由到 Portal
- 映射渠道用户到 Portal 用户 ID
- 应用速率限制和安全检查

### 渠道运行时 (`runtime.py`)
- 管理连接器生命周期
- 维护活跃连接池

---

## 持久化存储 (`src/storage/`)

### StorageManager (`persistence.py`)
- 多后端支持: File (JSON) / SQLite / PostgreSQL
- 集合: workflows, templates, plugins, agents, configs, approvals, artifact_candidates
- `compare_and_set()` 提供原子性条件更新 (乐观并发控制)
- SQLite 使用 `aiosqlite`; PostgreSQL 使用 `asyncpg` 加行级锁

---

## 插件系统补充

### MCP 管理 (`mcp_manager.py`)
- 全局 MCP 服务器注册表
- 持久化到 `data/mcp/registry.json`
- 支持服务器绑定到多个 Agent

### Skill 管理 (`skill_manager.py` + `skill_parser.py`)
- Skill 包格式: `.zip`/`.skill` 文件，含 `SKILL.md` YAML 元数据
- 存储在 `data/skills/{uuid}/` 目录
- 自动解析函数签名，构建 JSON Schema
- 支持依赖声明和审批流程

---

## 配置文件 (`config/default.yaml`)

```yaml
server:
  host: "0.0.0.0"
  port: 8000

execution:
  max_depth: 10
  total_timeout: 300  # seconds
  layer_timeout: 60
  error_strategy: "fail_fast"

agent:
  model: "gpt-4"
  temperature: 0.7
  max_tokens: 4096
  provider: "openai"

plugins:
  mcp:
    enabled: true
    timeout: 30
  skill:
    enabled: true
  rag:
    enabled: true
    default_top_k: 5
```

---

## API 使用示例

### 创建客户支持工作流
```python
from src.core.models import AgentType, AgentConfig, NativeAgentConfig, RoutingStrategy
from src.core.agent_node import AgentNode
from src.orchestration.workflow import get_workflow_manager

# 创建分诊 Agent (根节点)
triage_agent = AgentNode(
    name="triage_agent",
    description="路由客户咨询到专家",
    type=AgentType.NATIVE,
    config=AgentConfig(
        native_config=NativeAgentConfig(
            instructions="分析客户问题并路由到合适的专家...",
            model="gpt-4",
        )
    ),
    routing_strategy=RoutingStrategy.CONDITIONAL,
)

# 创建专家 Agent (子节点)
refund_specialist = AgentNode(
    name="refund_specialist",
    description="处理退款请求",
    type=AgentType.NATIVE,
    parent_id=triage_agent.id,
    ...
)

# 设置路由条件
triage_agent.set_routing_condition("refund", refund_specialist.id)

# 创建工作流
manager = get_workflow_manager()
workflow = await manager.create_workflow("Customer Support", root_agent=triage_agent)
workflow.add_agent(refund_specialist, triage_agent.id)

# 执行
await workflow.initialize()
result = await workflow.run("我想退货")
```

### 混合平台工作流
```python
# Native 协调者
coordinator = AgentNode(
    name="coordinator",
    type=AgentType.NATIVE,
    routing_strategy=RoutingStrategy.PARALLEL,
    ...
)

# Coze 专家
coze_agent = AgentNode(
    name="coze_specialist",
    type=AgentType.COZE,
    config=AgentConfig(
        coze_config=CozeConfig(bot_id="xxx", api_key="xxx")
    ),
    parent_id=coordinator.id,
)

# Dify 工作流
dify_agent = AgentNode(
    name="dify_workflow",
    type=AgentType.DIFY,
    config=AgentConfig(
        dify_config=DifyConfig(app_id="xxx", api_key="xxx", mode="workflow")
    ),
    parent_id=coordinator.id,
)
```

---

## 环境变量

```env
# OpenAI
OPENAI_API_KEY=your_api_key

# Azure OpenAI (可选)
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com/
AZURE_OPENAI_API_KEY=your_api_key

# Coze (可选)
COZE_BOT_ID=your_bot_id
COZE_API_KEY=your_api_key

# Dify (可选)
DIFY_APP_ID=your_app_id
DIFY_API_KEY=your_api_key

# 豆包 (可选)
DOUBAO_API_KEY=your_api_key
```

---

## 快速启动

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行示例
python examples/basic_workflow.py

# 启动 API 服务
python -m src.api.main
```

---

## 开发状态

- [x] 核心框架实现
- [x] 多平台适配器 (Native/Builtin/Coze/Dify/豆包/AutoGen)
- [x] 插件系统 (MCP/Skill/RAG)
- [x] 路由策略 (Sequential/Parallel/Conditional/Coordinator/Intent 等 9 种)
- [x] REST API (100+ 端点)
- [x] 执行事件流（用于可视化）
- [x] Web UI 编排界面 (ReactFlow)
- [x] 持久化存储 (SQLite / File / PostgreSQL)
- [x] 执行沙箱 (Docker + Local Process)
- [x] 人机协同审批系统
- [x] 工具治理 (Policy Engine + Approval)
- [x] 内置工具 (Web/Email/Filesystem/Shell)
- [x] Super Portal 超级入口 (意图理解 + 记忆 + 安全扫描)
- [x] AI Copilot (多轮对话生成工作流)
- [x] MemPalace MCP 记忆集成
- [x] 多渠道集成 (飞书/钉钉/Telegram/微信)
- [x] 产物工厂 (Learning Loop L1/L2/L3)
- [ ] 监控和可观测性

---

## 关键文件速查

| 功能 | 文件路径 |
|------|----------|
| 数据模型定义 | `src/core/models.py` |
| Agent 节点/树 | `src/core/agent_node.py` |
| 执行上下文 | `src/core/context.py` |
| 树形执行器 | `src/core/tree_executor.py` |
| 适配器基类 | `src/adapters/base.py` |
| 内置 Agent (多LLM) | `src/adapters/builtin.py` |
| 插件注册 | `src/plugins/registry.py` |
| MCP 管理 | `src/plugins/mcp_manager.py` |
| Skill 管理 | `src/plugins/skill_manager.py` |
| 工作流管理 | `src/orchestration/workflow.py` |
| 路由器 | `src/orchestration/router.py` |
| 结果聚合 | `src/orchestration/aggregator.py` |
| 工具执行器 | `src/execution/tool_executor.py` |
| 执行后端 (本地) | `src/execution/backends/local.py` |
| 执行后端 (Docker) | `src/execution/backends/docker.py` |
| 审批系统 | `src/governance/approval.py` |
| 策略引擎 | `src/governance/policy_engine.py` |
| 工具治理 | `src/governance/tool_governance.py` |
| 网页工具 | `src/tools/web.py` |
| 邮件工具 | `src/tools/email.py` |
| 文件系统工具 | `src/tools/filesystem.py` |
| Shell 工具 | `src/tools/shell.py` |
| Super Portal 服务 | `src/portal/service.py` |
| 意图理解 | `src/portal/intent.py` |
| 安全扫描 | `src/portal/safety.py` |
| 轨迹提取 | `src/portal/trajectory.py` |
| MemPalace 记忆 | `src/portal/mempalace_memory_provider.py` |
| AI Copilot | `src/copilot/service.py` |
| 产物工厂 | `src/artifacts/service.py` |
| 渠道网关 | `src/integrations/gateway.py` |
| 渠道运行时 | `src/integrations/runtime.py` |
| 飞书连接器 | `src/integrations/connectors/feishu.py` |
| 微信连接器 | `src/integrations/connectors/weixin.py` |
| 持久化存储 | `src/storage/persistence.py` |
| REST API | `src/api/main.py` |
| 前端主应用 | `ui/src/App.tsx` |
| 前端 API 客户端 | `ui/src/api/client.ts` |
| 工作流编辑器 | `ui/src/components/WorkflowEditor.tsx` |
| 超级入口对话 | `ui/src/components/PortalChat.tsx` |

---

## 技术栈

### 后端
- **Python 3.11+**
- **Pydantic**: 数据验证和序列化
- **FastAPI**: REST API 框架
- **aiohttp**: 异步 HTTP 客户端
- **aiosqlite**: 异步 SQLite
- **asyncio**: 异步执行
- **Microsoft Agent Framework**: Agent 基础框架
- **OpenAI SDK**: LLM 调用 (支持多提供商)
- **MCP (Model Context Protocol)**: 插件协议

### 前端
- **React 18**: UI 框架
- **TypeScript**: 类型安全
- **Vite**: 构建工具
- **ReactFlow**: 可视化编排
- **Zustand**: 状态管理
- **Axios**: HTTP 客户端
- **CSS Modules**: 样式隔离

---

## Web UI 组件

### 主应用 (`ui/src/App.tsx`)
5 个主要页面:
- **AI助手** (RootPortalChat): 默认超级入口对话
- **工作流列表** (WorkflowList): 工作流管理 + 模板
- **超级入口** (PortalList/PortalChat): Portal CRUD + 对话
- **技能库** (SkillMarket): 技能管理
- **系统设置** (SettingsPanel): 搜索/邮件/Copilot 配置

### 工作流编辑器 (`ui/src/components/WorkflowEditor.tsx`)
- ReactFlow 可视化编排
- Agent 节点拖拽和连线
- 模态框: 创建 Agent / 从模板创建 / 创建新工作流
- 发布/取消发布
- Agent 编辑器 (双击节点)
- 执行面板 (SSE 事件流)
- Copilot 面板 (AI 辅助生成工作流)

### 超级入口对话 (`ui/src/components/PortalChat.tsx`)
- SSE 实时事件流
- 事件卡片: 意图理解、工作流调度、节点执行、结果综合
- 执行时间线侧边栏
- 渠道接入模态框 (Telegram/钉钉/飞书/微信扫码)
- 会话恢复 (localStorage 缓存)

### 超级入口管理 (`ui/src/components/PortalList.tsx`)
- Portal 列表 (卡片网格)
- 创建/编辑 Portal (绑定工作流、LLM 配置、记忆配置)
- MemPalace 配置: Palace Path, Wing 策略, Default Room
- 访问密钥模态框 (Portal ID + Access Key + API 端点)
