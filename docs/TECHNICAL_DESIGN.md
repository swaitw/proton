# Proton - 树形 Agent 平台技术方案

## 1. 项目概述

Proton 是一个基于 Microsoft Agent Framework 构建的树形 Agent 编排平台，支持：
- 树形 Agent 群架构（主 Agent 下挂多层子 Agent）
- 集成多种第三方 Agent 服务（豆包、Coze、Dify 等）
- 支持自研 Agent（基于 AutoGen 等框架）
- 统一的 MCP、Skill、RAG 挂载能力
- 可视化 Agent 编排界面

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web UI (React/Vue)                        │
│                    Agent 编排 & 可视化界面                         │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      REST API (FastAPI)                          │
│            Agent 管理 / Workflow 执行 / 配置管理                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Orchestration Engine                           │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Tree-based Agent Executor                   │    │
│  │    处理深层嵌套 / 调用链管理 / 上下文传递 / 结果聚合          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│    ┌─────────────────────────┼─────────────────────────┐        │
│    ▼                         ▼                         ▼        │
│  ┌─────────┐           ┌─────────┐           ┌─────────┐        │
│  │ Agent 1 │           │ Agent 2 │           │ Agent N │        │
│  │ (Master)│           │ (Child) │           │ (Child) │        │
│  └────┬────┘           └────┬────┘           └────┬────┘        │
│       │                     │                     │             │
│       ▼                     ▼                     ▼             │
│  ┌─────────┐           ┌─────────┐           ┌─────────┐        │
│  │Sub-Agent│           │Sub-Agent│           │Sub-Agent│        │
│  └─────────┘           └─────────┘           └─────────┘        │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Unified Adapter Layer                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  Native  │ │   Coze   │ │   Dify   │ │  Doubao  │ │AutoGen │ │
│  │  Agent   │ │ Adapter  │ │ Adapter  │ │ Adapter  │ │Adapter │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Plugin System                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  MCP Plugin  │  │ Skill Plugin │  │  RAG Plugin  │           │
│  │  (Tools)     │  │  (Actions)   │  │  (Context)   │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

## 3. 核心设计

### 3.1 树形 Agent 节点结构

```python
@dataclass
class AgentNode:
    id: str                          # 唯一标识
    name: str                        # 显示名称
    type: AgentType                  # agent 类型 (native/coze/dify/doubao/autogen)
    config: AgentConfig              # 配置信息
    parent_id: Optional[str]         # 父节点 ID (None 表示根节点)
    children: List[str]              # 子节点 ID 列表
    plugins: List[PluginConfig]      # 挂载的插件 (MCP/Skill/RAG)
    routing_strategy: RoutingStrategy # 子 Agent 调用策略
    max_depth: int                   # 最大递归深度 (防止无限嵌套)
    timeout: float                   # 执行超时时间
    retry_policy: RetryPolicy        # 重试策略
```

### 3.2 深层嵌套问题解决方案

1. **最大深度限制**: 每个节点配置 `max_depth`，防止无限递归
2. **调用链追踪**: 使用 `CallChain` 记录完整调用路径，检测循环调用
3. **上下文压缩**: 深层嵌套时自动压缩历史上下文，保留关键信息
4. **超时控制**: 每层调用有独立超时，总超时 = Σ(各层超时)
5. **异步并发**: 支持并行调用同级子 Agent

```python
@dataclass
class CallChain:
    chain: List[str]           # 调用链 [root_id, child_id, ...]
    depth: int                 # 当前深度
    start_time: float          # 开始时间
    context_tokens: int        # 上下文 token 数

    def check_cycle(self, agent_id: str) -> bool:
        """检测循环调用"""
        return agent_id in self.chain

    def check_depth(self, max_depth: int) -> bool:
        """检查深度限制"""
        return self.depth >= max_depth
```

### 3.3 Agent 间调用逻辑

```
┌─────────────────────────────────────────────────────────────┐
│                    Routing Strategies                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Sequential (顺序执行)                                   │
│     A -> B -> C -> Result                                   │
│     适用：流水线处理                                         │
│                                                             │
│  2. Parallel (并行执行)                                     │
│     A -> [B, C, D] -> Aggregate -> Result                   │
│     适用：独立任务并行处理                                   │
│                                                             │
│  3. Conditional (条件路由)                                  │
│     A -> Classifier -> B or C or D -> Result                │
│     适用：根据输入类型分发                                   │
│                                                             │
│  4. Handoff (交接模式)                                      │
│     Triage -> Specialist1 <-> Specialist2 -> Result         │
│     适用：多专家协作                                         │
│                                                             │
│  5. Hierarchical (层级分解)                                 │
│     Master -> [SubTask1, SubTask2] -> Merge -> Result       │
│     适用：复杂任务分解                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.4 Adapter 统一接口

所有第三方 Agent 必须实现统一的 `AgentAdapter` 接口：

```python
class AgentAdapter(ABC):
    @abstractmethod
    async def run(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs
    ) -> AgentResponse:
        """执行 Agent"""
        pass

    @abstractmethod
    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs
    ) -> AsyncIterator[AgentResponseUpdate]:
        """流式执行 Agent"""
        pass

    @abstractmethod
    def get_capabilities(self) -> AgentCapabilities:
        """获取 Agent 能力描述"""
        pass
```

### 3.5 Plugin 系统

```python
class PluginRegistry:
    """插件注册中心"""

    plugins: Dict[str, Plugin] = {}

    def register_mcp(self, server_config: MCPServerConfig) -> MCPPlugin:
        """注册 MCP 服务器"""
        pass

    def register_skill(self, skill_func: Callable) -> SkillPlugin:
        """注册 Skill 函数"""
        pass

    def register_rag(self, rag_config: RAGConfig) -> RAGPlugin:
        """注册 RAG 服务"""
        pass

    def get_tools_for_agent(self, agent_id: str) -> List[Tool]:
        """获取 Agent 可用的所有工具"""
        pass
```

## 4. 数据模型

### 4.1 Agent 配置模型

```python
class AgentConfig(BaseModel):
    # 基础配置
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 4096

    # Agent 类型特定配置
    native_config: Optional[NativeAgentConfig] = None
    coze_config: Optional[CozeConfig] = None
    dify_config: Optional[DifyConfig] = None
    doubao_config: Optional[DoubaoConfig] = None
    autogen_config: Optional[AutoGenConfig] = None

    # 插件配置
    mcp_servers: List[MCPServerConfig] = []
    skills: List[SkillConfig] = []
    rag_sources: List[RAGSourceConfig] = []
```

### 4.2 Workflow 配置模型

```python
class WorkflowConfig(BaseModel):
    id: str
    name: str
    description: str

    # 树形结构
    root_agent_id: str
    agents: Dict[str, AgentNode]

    # 全局配置
    global_context: Dict[str, Any] = {}
    max_total_depth: int = 10
    total_timeout: float = 300.0

    # 执行配置
    execution_mode: ExecutionMode = ExecutionMode.ASYNC
    error_handling: ErrorHandlingStrategy = ErrorHandlingStrategy.FAIL_FAST
```

## 5. API 设计

### 5.1 Agent 管理 API

```
POST   /api/agents                    # 创建 Agent
GET    /api/agents                    # 列出所有 Agent
GET    /api/agents/{id}               # 获取 Agent 详情
PUT    /api/agents/{id}               # 更新 Agent
DELETE /api/agents/{id}               # 删除 Agent
POST   /api/agents/{id}/test          # 测试 Agent
```

### 5.2 Workflow 管理 API

```
POST   /api/workflows                 # 创建 Workflow
GET    /api/workflows                 # 列出所有 Workflow
GET    /api/workflows/{id}            # 获取 Workflow 详情
PUT    /api/workflows/{id}            # 更新 Workflow
DELETE /api/workflows/{id}            # 删除 Workflow
POST   /api/workflows/{id}/run        # 执行 Workflow
GET    /api/workflows/{id}/status     # 获取执行状态
POST   /api/workflows/{id}/visualize  # 生成可视化图
```

### 5.3 Plugin 管理 API

```
POST   /api/plugins/mcp               # 注册 MCP 服务
POST   /api/plugins/skill             # 注册 Skill
POST   /api/plugins/rag               # 注册 RAG 源
GET    /api/plugins                   # 列出所有插件
DELETE /api/plugins/{id}              # 删除插件
```

## 6. 技术选型

| 组件 | 技术选择 | 说明 |
|------|---------|------|
| Agent Framework | Microsoft Agent Framework | 核心编排框架 |
| Web Framework | FastAPI | 高性能异步 API |
| 数据存储 | SQLite/PostgreSQL | 配置持久化 |
| 缓存 | Redis | 会话状态缓存 |
| 前端 | React + Ant Design | 可视化编排 UI |
| 可视化 | React Flow | 流程图编辑器 |
| 序列化 | Pydantic | 数据验证和序列化 |

## 7. 目录结构

```
proton/
├── src/
│   ├── core/                    # 核心模块
│   │   ├── __init__.py
│   │   ├── models.py            # 数据模型
│   │   ├── agent_node.py        # Agent 节点
│   │   ├── tree_executor.py     # 树形执行器
│   │   └── context.py           # 执行上下文
│   │
│   ├── adapters/                # Agent 适配器
│   │   ├── __init__.py
│   │   ├── base.py              # 基础适配器接口
│   │   ├── native.py            # 原生 Agent
│   │   ├── coze.py              # Coze 适配器
│   │   ├── dify.py              # Dify 适配器
│   │   ├── doubao.py            # 豆包适配器
│   │   └── autogen.py           # AutoGen 适配器
│   │
│   ├── plugins/                 # 插件系统
│   │   ├── __init__.py
│   │   ├── registry.py          # 插件注册中心
│   │   ├── mcp_plugin.py        # MCP 插件
│   │   ├── skill_plugin.py      # Skill 插件
│   │   └── rag_plugin.py        # RAG 插件
│   │
│   ├── orchestration/           # 编排引擎
│   │   ├── __init__.py
│   │   ├── router.py            # 路由策略
│   │   ├── aggregator.py        # 结果聚合
│   │   └── workflow.py          # Workflow 构建
│   │
│   ├── api/                     # REST API
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI 应用
│   │   ├── agents.py            # Agent API
│   │   ├── workflows.py         # Workflow API
│   │   └── plugins.py           # Plugin API
│   │
│   └── ui/                      # Web UI (可选)
│       └── ...
│
├── config/
│   ├── default.yaml             # 默认配置
│   └── adapters/                # 适配器配置模板
│
├── tests/
│   ├── test_adapters.py
│   ├── test_orchestration.py
│   └── test_plugins.py
│
├── docs/
│   └── TECHNICAL_DESIGN.md      # 本文档
│
├── requirements.txt
└── README.md
```

## 8. 实现优先级

### Phase 1: 核心框架
1. 数据模型定义
2. Native Agent 适配器
3. 树形执行器基础实现
4. 基础 API

### Phase 2: 第三方集成
1. Coze 适配器
2. Dify 适配器
3. 豆包适配器
4. AutoGen 适配器

### Phase 3: 插件系统
1. MCP 插件支持
2. Skill 插件支持
3. RAG 插件支持

### Phase 4: 可视化
1. Web UI 框架
2. 流程图编辑器
3. 实时执行监控
