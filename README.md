# Proton Agent Platform

基于 Microsoft Agent Framework 的树形 Agent 编排平台。

## 特性

- **树形 Agent 架构**: 支持主 Agent 下挂多层子 Agent，复杂任务分解与意图路由（Intent Routing）
- **多平台集成**: 支持 Native、Builtin、Coze、Dify、豆包、AutoGen 等多种 Agent 来源
- **沙箱隔离执行**: 支持基于 Docker 的 Python 代码执行沙箱，杜绝危险工具逃逸
- **插件与技能系统**: 支持 MCP、Skill（可由 LLM 自动生成与学习沉淀）、RAG 等插件挂载
- **深层嵌套保护**: 并发状态隔离（Context Isolation）、循环检测、深度限制、上下文快照压缩
- **REST API**: 完整的 API 支持，方便集成
- **可视化编排与超级入口**: Web UI 支持 Agent 关系编排，并提供 Portal 统一超级入口体验

## 快速开始

### 安装

```bash
# 克隆项目
cd proton

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 配置

创建 `.env` 文件:

```env
# OpenAI
OPENAI_API_KEY=your_api_key

# Azure OpenAI (可选)
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
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

### 运行示例

```bash
# 运行基础示例
python examples/basic_workflow.py

# 启动 API 服务器
python -m src.api.main
```

### MemPalace MCP 自检

如果你启用了 Portal 长期记忆（MemPalace），建议在当前 Python 环境执行一次自检：

```bash
python scripts/check_mempalace_mcp.py
```

通过标准：
- 能 `import mempalace.mcp_server`
- 能执行 `python -m mempalace.mcp_server --help`
- `MemPalaceClient.ensure_ready()` 返回 ready 且发现工具列表

说明：生产/分发环境请保持 `mempalace_command=python`、`mempalace_args=["-m","mempalace.mcp_server"]`，并确保 Proton 与 MemPalace 安装在同一环境中。

### 飞书接入与配对

以下流程用于将 `Root Portal` 连接到飞书机器人（长连接模式）。

1. 飞书开放平台配置（企业自建应用）
- 事件接收方式：`长连接（WebSocket）`
- 订阅事件：`im.message.receive_v1`
- 应用发布：权限和事件配置完成后，务必“发布版本”

2. 飞书权限（建议最小可用集合）
- `contact:user.base:readonly`
- `im:chat`
- `im:message`
- `im:message.group_at_msg:readonly`
- `im:message.group_msg`
- `im:message.p2p_msg:readonly`

3. 在 Proton 里绑定飞书渠道
- 通过 Web UI 的 Portal 渠道设置填写 `app_id`、`app_secret` 并启用 `feishu`
- 或调用接口：

```bash
curl -X PUT http://127.0.0.1:8000/api/portals/{portal_id}/channels/feishu \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "config": {
      "app_id": "cli_xxx",
      "app_secret": "xxx"
    }
  }'
```

4. 生成配对码并在飞书私聊发送
- 配对码获取位置：
  - 在 Web UI 的飞书渠道面板点击“生成配对码”
  - 或调用接口，返回体里的 `pairing_code` 就是要发送给机器人的码

```bash
curl -X POST http://127.0.0.1:8000/api/portals/{portal_id}/channels/feishu/pairing \
  -H "Content-Type: application/json" \
  -d '{"ttl_seconds": 1800}'
```

- 用户在飞书里给机器人发送该码，成功后会收到“已配对成功，可以开始对话”
- 可通过接口查看当前绑定用户（`allowed_users`）：

```bash
curl http://127.0.0.1:8000/api/portals/{portal_id}/channels/feishu/allowlist
```

5. 关于重启与重新配对
- 服务重启后通常不需要重新配对
- 原因：成功配对后用户 `open_id` 会落到 `allowed_users` 并持久化
- 只有删除渠道绑定、清空存储或切换到新 portal 时，才需要重新配对

### API 使用

```bash
# 创建工作流
curl -X POST http://localhost:8000/api/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Workflow",
    "description": "A simple workflow",
    "root_agent": {
      "name": "coordinator",
      "description": "Main coordinator agent",
      "type": "native"
    }
  }'

# 添加子 Agent
curl -X POST http://localhost:8000/api/workflows/{workflow_id}/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "specialist",
    "description": "Specialist agent",
    "type": "native",
    "parent_id": "{root_agent_id}"
  }'

# 运行工作流
curl -X POST http://localhost:8000/api/workflows/{workflow_id}/run \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, I need help!"
  }'
```

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                      Web UI / API                        │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                  Orchestration Engine                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │           Tree-based Agent Executor              │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      ┌─────────┐     ┌─────────┐     ┌─────────┐
      │ Agent 1 │     │ Agent 2 │     │ Agent N │
      │ (Root)  │     │ (Child) │     │ (Child) │
      └─────────┘     └─────────┘     └─────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────┐
│                    Adapter Layer                         │
│  Native | Coze | Dify | Doubao | AutoGen | Custom       │
└─────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────┐
│                    Plugin System                         │
│         MCP Tools | Skills | RAG Context                │
└─────────────────────────────────────────────────────────┘
```

## 路由策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| Sequential | 顺序执行子 Agent，结果传递 | 流水线处理 |
| Parallel | 并行执行子 Agent，上下文状态隔离防竞态 | 独立子任务/批量搜集 |
| Conditional | 根据条件匹配路由 | 分类任务 |
| Handoff | 转移控制权/专家交接 | 多专家协作转移 |
| Coordinator | 汇总多 Agent 结果后再综合 | 综合汇报 |
| Intent | **LLM 意图识别 + 子查询重写 + 并发优先级路由** | 复杂任务动态分发（Super Portal 核心） |

## 项目结构

```
proton/
├── src/
│   ├── core/           # 核心抽象
│   │   ├── models.py       # Pydantic 模型
│   │   ├── agent_node.py   # Agent 节点
│   │   ├── context.py      # 上下文管理与并发隔离
│   │   └── tree_executor.py# 树形执行器（编排内核）
│   │
│   ├── execution/      # 执行平面与沙箱
│   │   ├── backends/       # Docker / Local 执行后端
│   │   └── tool_executor.py# 统一工具执行器
│   │
│   ├── adapters/       # Agent 适配器层
│   │   ├── native.py       # 原生 Agent
│   │   ├── builtin.py      # 内置 OpenAI-compatible 工具链 Agent
│   │   ├── coze.py / dify.py / doubao.py  # 第三方平台
│   │   └── autogen.py / workflow.py       # AutoGen / 子工作流
│   │
│   ├── plugins/        # 插件系统
│   │   ├── mcp_plugin.py   # MCP 协议支持
│   │   ├── skill_plugin.py # Python 函数技能
│   │   └── rag_plugin.py   # 向量检索
│   │
│   ├── portal/         # Super Portal (超级入口)
│   │   ├── intent.py       # LLM 意图理解与分发
│   │   ├── memory.py       # 多层记忆体系 (Hot/Warm/Cold + 冲突合并)
│   │   └── trajectory.py   # 轨迹收集与触发学习
│   │
│   ├── artifacts/      # 生成物与学习闭环
│   │   └── service.py      # 沉淀程序化技能 (LLM Write Code)
│   │
│   ├── governance/     # 治理与策略平面
│   │   ├── policy_engine.py# 安全策略与访问控制
│   │   └── approval.py     # Human-in-the-loop 审批机制
│   │
│   └── api/            # FastAPI 接口
│       └── main.py
```

## 演进方向 (Code Plan)

Proton 正在向“长期运行的 Agent 平台 (Always-on)”演进，近期核心里程碑已达成：
- [x] **执行平面分层**：废弃 `exec()`，落地 Docker 隔离后端。
- [x] **上下文并发安全**：通过深度拷贝实现了 Parallel / Intent 并发状态隔离。
- [x] **自我改进学习**：打通了从 Trajectory 总结到 LLM 生成真实 Skill 代码并落盘的闭环。
- [x] **记忆与上下文治理**：引入 TTL 记忆层、冲突检测与稳定快照注入。
- [ ] **执行审批链路 (UI 联动)**：强化 PolicyEngine 与前端 ExecutionPanel 的强制审批闭环。
- [ ] **回放与评测 (Replay & Benchmark)**：落地离线回归测试。

## 运行期产物说明

- `data/skills/*` 属于运行时生成产物（例如自动生成 skill 包、安装缓存等），不应纳入代码版本管理。
- 仓库已默认忽略该目录下内容，仅保留 `data/skills/registry.json` 作为可选登记文件。
- 若需要备份运行时产物，建议通过对象存储或制品库单独归档，而不是直接提交到 Git。

## License

MIT License
