# 基于 OpenClaw 与 Hermes Agent 的技术架构演变：未来半年 AI 智能体发展延展方向（技术团队规划版）

> 目标：以 **OpenClaw**（偏“本地优先的个人助理控制平面”）与 **Hermes Agent**（偏“自我改进 + 可迁移的云端持久智能体”）两条路线的架构演进为参照，抽象未来 6 个月（~2 个迭代季度）智能体系统最可能的“能力延展方向”，并给出技术落地路线、工程抓手、风险与衡量指标。  
> 受众：架构负责人、Agent 平台/基础设施团队、工具/安全/评测团队。  

---

## 0. 结论先行（Executive Summary）

未来半年，AI 智能体（Agent）系统的演进将从“能调用工具完成任务”快速转向“**可长期运行、可治理、可度量、可进化**”的工程系统。对技术团队而言，最关键的不是再堆更多工具，而是围绕 **控制平面（control plane）** + **执行平面（execution plane）** + **记忆/技能平面（knowledge plane）** + **治理平面（safety & observability plane）** 四个面建立可迭代的架构骨架。

结合两套系统的演进信号：

1. **OpenClaw** 强调“本地优先、强通道接入、明确安全默认、可插拔技能/插件、终端优先的可控 onboarding”，其 Agent Runtime 通过 workspace 的 bootstrap 文件（AGENTS.md/SOUL.md/TOOLS.md/…）把“长期规则”外置成可编辑的事实源，并将 MCP 通过桥接（mcporter）解耦，避免核心被生态波动拖累。  
2. **Hermes Agent** 在此基础上更进一步：把 Agent 从“本地常驻”拓展到“可跑在 $5 VPS、GPU 集群、serverless 持久环境”，并把 **学习闭环**（技能自生成/自改进、记忆写入 nudges、会话全文检索与摘要、用户建模）与 **评测/数据生成**（trajectory、RL environment）直接纳入核心架构版图。其架构文档明确系统已不再是“一个 chat loop + tools”，而是包含 prompt caching、压缩谱系、provider runtime 解析、gateway 语义、ACP（编辑器集成）、cron 任务等多子系统。  

据此，未来 6 个月最值得押注的 8 个延展方向（按优先级）：

1. **Agent 控制平面产品化**：把“会话/通道/身份/权限/工具集/工作区/计划任务”统一建模成可观测、可回滚、可迁移的控制平面（OpenClaw Gateway 与 Hermes Gateway 均在强化）。  
2. **执行环境与沙箱的分层**：将“工具调用”从“同机执行”演进为“可切换的执行后端”（本地、Docker、SSH、Daytona、Modal 等），并把审批/策略/隔离当成一等公民。  
3. **技能作为可治理的程序化记忆（procedural memory）**：技能不只是 prompt 模板，而是带元数据、依赖、验证步骤、可审计、安全扫描、可发布/可回滚的能力包（Hermes 的 skills hub 与 progressive disclosure 是明确方向）。  
4. **记忆从“文件注入”走向“多层记忆体系”**：短期保持 bounded memory（token 预算固定）+ session 搜索；中期引入外部 memory provider（KG、实体解析、语义检索）并形成统一检索路由与注入策略。  
5. **上下文工程化（Context Engineering）成为核心竞争力**：prompt assembly、压缩、缓存、谱系、冷热分层、引用机制将成为“成本/稳定性/可控性”的决定因素。  
6. **可度量与可复现（Trajectories & Evaluation）**：把工具轨迹、决策点、失败案例、修复策略结构化记录，既服务 debugging/回归，也为 RL/SFT 数据生成服务（Hermes 已内置 batch_runner 与 environments）。  
7. **多前端一致性与“打断-重定向”交互**：跨 CLI/IM/语音/Canvas/IDE 的一致语义（stop/undo/retry/steer、流式工具输出、chunking）会成为“长期可用”的体验基座。  
8. **标准与生态接口（MCP/ACP/skills spec）**：未来半年主流 Agent 会更主动拥抱协议化扩展，减少“把所有集成都写进核心”的维护成本与安全面。  

---

## 1. 参照系：OpenClaw 与 Hermes Agent 的架构取向差异

### 1.1 OpenClaw：本地优先的 Gateway + 内嵌 Agent Runtime

从官方 README 与 Agent Runtime 文档可以抽象出 OpenClaw 的几个关键设计点：

1. **Gateway 是控制平面，助手才是产品本体**：OpenClaw 把“多通道收发、会话路由、工具编排、事件与 UI”汇聚到一个本地 Gateway（长期常驻），并强调它运行在用户设备上、遵循用户规则（隐私/安全默认）。  
2. **Agent workspace 强约束**：Agent 的工具执行与上下文都被限定在 `agents.defaults.workspace`，作为唯一的工作目录（cwd），并通过 bootstrap 文件注入规则/人格/工具说明/用户画像：  
   - `AGENTS.md`（操作指令 + “记忆”）  
   - `SOUL.md`（persona、边界、语气）  
   - `TOOLS.md`（工具使用约定）  
   - `USER.md`（用户偏好）  
   - `IDENTITY.md`、`BOOTSTRAP.md` 等  
   这些文件在新会话首轮被注入，空文件跳过、过大截断，保证 prompt 体积可控。  
3. **插件与技能分层**：Vision 文档强调“核心保持精简，能力尽量以插件形式外置”，并指出 memory 是一个“特殊插件槽位（只能启用一个）”，长期会收敛到推荐默认路径；技能则鼓励发布到生态（ClawHub）而非持续堆到 core。  
4. **MCP 采取桥接解耦**：OpenClaw 通过 `mcporter` 集成 MCP，以“可热插拔、减少核心 churn、降低安全与稳定风险”为优先，而非将 MCP runtime 深度内嵌进核心。  
5. **路线图的第一优先级是安全默认 + 稳定 + onboarding**：Vision 中明确当前 focus 是安全与可用性，而非盲目上更多“花活架构”。  

以上点位共同构成 OpenClaw 的“工程哲学”：**强控制平面 + 受限工作区 + 安全默认 + 可插拔扩展**。

### 1.2 Hermes Agent：云端持久化 + 学习闭环 + 评测/训练一体化

Hermes README 与架构文档透露的方向更“激进”：

1. **“自我改进”是产品卖点**：Hermes 宣称自己具备内置学习闭环：从经验创建技能、使用中改进技能、主动写入记忆、跨会话检索历史对话并总结，以及建立更深的用户模型。  
2. **运行形态从“本地常驻”扩展到“可在多种执行后端持久存在”**：Hermes 支持多种 terminal 后端（本地、Docker、SSH、Daytona、Singularity、Modal），强调 serverless persistence（空闲休眠、唤醒）以降低成本与运维摩擦。  
3. **多子系统架构清晰化**：架构地图显示核心仓库包含 agent loop、prompt building/缓存/压缩、provider runtime 解析、工具运行时、SQLite 会话存储、gateway、cron、ACP（IDE 集成）、RL environments、batch trajectory runner 等。  
4. **记忆体系分层**：  
   - 内置 bounded memory：`MEMORY.md` 与 `USER.md` 固定字符上限，并采用“冻结快照注入”（会话开始注入，过程中更新写盘，下次会话才生效）以保护 prefix cache 性能。  
   - Session Search：SQLite FTS5 全文检索 + LLM 摘要，用于“无限容量”的历史回忆。  
   - 外部 memory providers：Honcho、Hindsight、Mem0 等，以插件形式提供知识图谱、实体解析、多策略检索等能力。  
5. **技能系统更接近“能力包/协议”**：技能采用 progressive disclosure（list → view → view file），遵循 agentskills.io 标准，支持 hub 安装、来源审计、安全扫描、信任级别、条件启用（fallback/required toolsets），并提供 agent-managed skill_manage 工具作为程序化记忆。  
6. **迁移路径：从 OpenClaw 到 Hermes**：Hermes 提供 `hermes claw migrate`，说明其在生态上承接 OpenClaw 的用户资产（配置、记忆、技能、allowlist、API keys 等）。这意味着两者并非完全平行，而是存在“架构与产品形态的迭代/再组织”。  

Hermes 的工程哲学可以概括为：**把“长期可用的个人 Agent”当成一个可进化的分布式系统来做**，并且把“评测与数据生成”前置到架构层。

---

## 2. “技术架构演变”的共性趋势（从两者交集抽象）

从 OpenClaw → Hermes（以及两者文档中明确强调的点）可以抽象出 6 个共性演进趋势。这些趋势基本就是未来半年 Agent 系统会继续加速的方向。

### 2.1 从单回合对话系统 → 长期运行系统（Always-on）

过去很多 Agent 框架把“对话”当主循环，工具调用只是函数调用扩展；而 OpenClaw/Hermes 更像在做“长期运行的控制平面”：

- **会话（session）** 变成一等实体：有 ID、存储、压缩、谱系、可搜索、可迁移。  
- **通道（channels）** 不只是 IO，而是带有身份、allowlist、DM pairing、群规则、chunking/streaming 等语义。  
- **计划任务（cron）**、webhooks、自动化被纳入同一 Agent 语义（不是跑一个外部脚本）。  

> 影响：技术团队要用“分布式系统/控制平面”的思维来做 Agent，而不是“写一个更聪明的 prompt”。

### 2.2 从“工具调用” → “可插拔执行后端（Execution Backends）”

Hermes 明确提供多种 terminal backends；OpenClaw 强化工作区约束与 tool policy。共同指向：

- 执行环境需要可替换：本地（低延迟）/远端（高权限或专用资源）/容器（隔离）/serverless（成本）。  
- 同一 toolset 在不同 backend 需要一致的 API 与可观测性（stdout/stderr、文件、网络、退出码、资源使用）。  

> 影响：未来半年，Agent 平台的“工具层”会从零散脚本走向“执行平面”，并出现更标准的 sandbox/权限/策略组件。

### 2.3 从“提示词模板” → “技能（Skill）作为可治理资产”

Hermes 将技能视为 agentskills.io 兼容的能力包，具备元数据、脚本、参考文件、条件启用、安全扫描、hub 分发；OpenClaw 也强调技能应更多外置到生态，而不是进 core。共同指向：

- 技能是 **程序化记忆**：把成功路径固化成可重复执行的过程与验证步骤。  
- 技能需要 **治理能力**：版本、来源、审计、权限、依赖、风险评级。  

> 影响：技术团队要把技能当成“内部 package ecosystem”，配套 CI、签名/审计、回滚、灰度、度量。

### 2.4 从“无限记忆幻想” → “多层记忆体系（bounded + search + external）”

Hermes 的 memory 设计很典型：  
bounded memory（固定预算、可注入、稳定缓存） + session search（无限容量但按需） + external providers（更强但更复杂）。

OpenClaw 同样把记忆与规则外置到 workspace 文件，并将 memory 插件化，避免核心绑定单一路线。

> 影响：未来半年更可行的路线不是“塞更多上下文”，而是“分层 + 路由 + 引用 + 缓存”。

### 2.5 从“能跑” → “可观测、可打断、可修复”

Hermes 强调“可观察且可打断的工具执行”，并在 CLI/消息平台提供 stop/undo/retry、流式输出、工具输出流；OpenClaw 同样强调 streaming/chunking 与队列模式（steer/followup/collect）。共同指向：

- Agent 交互必须支持 **人类在环的实时操控**（steering、interrupt、approval）。  
- 调试与回归需要结构化轨迹与日志。  

### 2.6 从“使用模型” → “为训练/评测生产数据”

Hermes 直接把 batch trajectory generation、RL environments 放进代码结构；这反映了一个趋势：  
**Agent 系统本身将成为训练数据工厂**。未来半年会出现越来越多“生产可用 Agent”与“数据/评测/训练”融合的工程需求：

- 线上真实任务轨迹脱敏/抽样/压缩  
- 离线回放（replay）与一致性检查  
- 能力回归基准（benchmark suite）  
- 针对工具调用与安全策略的 RL/SFT 微调  

---

## 3. 未来半年（6个月）AI 智能体的 10 个延展方向（技术维度）

下面给出更细的“方向 → 具体工程抓手 → 风险点 → 产出指标”。

### 方向 1：控制平面（Control Plane）统一建模与状态机化

**核心问题**：当 Agent 需要跨通道、跨设备、跨执行后端持续运行时，如果控制平面没有统一模型，很快会陷入“配置散落、状态不可复现、权限不可审计、问题不可定位”。

**工程抓手**：

1. **统一资源模型**（建议最小集）：  
   - Identity（用户/账号/设备/通道）  
   - Session（会话：生命周期、压缩谱系、关联通道、上下文引用）  
   - Workspace（工作区：路径/后端/权限/配额/敏感域）  
   - Toolset（工具集：允许列表、策略、审批规则、沙箱配置）  
   - Skill（能力包：版本/来源/信任级/依赖/审计记录）  
   - Job（计划任务：触发器、输出渠道、幂等、失败策略）  
2. **状态机**：把 onboarding、pairing、approval、job execution、session compression、provider failover 等流程显式化为状态机（而非散落的 if/else）。  
3. **可迁移与可导出**：控制平面应支持“导出/导入/迁移”（Hermes 对 OpenClaw 的迁移是明确需求信号）。  
4. **审计日志与变更历史**：配置变更、权限调整、技能安装/升级、执行后端切换要能追溯。

**风险点**：

- 过度抽象导致难以落地；应先从最小资源模型开始  
- 统一模型会触碰大量历史代码，需要兼容层与迁移工具  

**指标**：

- 关键流程（pairing、执行后端切换、技能安装）可以“可重复复现”  
- 线上问题定位时间（MTTR）下降  
- 迁移时间：从一台机器迁移到另一台（或从本地迁到 VPS）< 30 分钟

---

### 方向 2：执行平面分层：工具运行时 → 作业运行时 → 沙箱与策略

**核心问题**：工具调用越来越强（读写文件、执行命令、浏览器自动化、访问账户），安全与稳定必须从“prompt 约束”升级为“工程策略”。

**工程抓手**：

1. **执行后端抽象**：统一接口：`run(command|code|browser_action|file_op)`，对不同 backend 做适配（本地/容器/SSH/serverless）。  
2. **策略引擎（Policy Engine）**：  
   - allowlist/denylist（命令、域名、路径、网络目的地）  
   - 人类审批门（高风险操作必须确认）  
   - 速率限制/配额（网络、文件大小、token）  
3. **作业（Job）语义**：把多工具步骤封装成可重试、可回滚、可中断的 job：  
   - 分阶段 checkpoint  
   - 幂等 key（避免重复下单/重复删除）  
   - 失败分流（自动降级到只读/只规划模式）  
4. **沙箱隔离**：  
   - 文件系统：工作区隔离、临时目录策略  
   - 网络：域名白名单、禁内网扫描  
   - 进程：资源限制（CPU/内存/超时）  

**风险点**：

- 过严策略影响可用性；需要分级策略（可信场景/不可信输入）  
- 多 backend 一致性复杂；需要先保证“观测一致性”（日志/结果结构）再追求特性一致  

**指标**：

- 高风险动作的“未审批执行率”趋近 0  
- 工具失败可自动重试/降级，成功率提升  
- 新增 backend 的接入周期（从评估到可用）< 2 周

---

### 方向 3：技能系统工程化：从 Prompt 模板 → 可发布能力包（Capability Package）

**核心问题**：规模化后，“技能”会像内部 SDK 一样爆炸增长；没有治理会造成 supply chain 风险与质量不可控。

**工程抓手**（参考 Hermes 的技能规范、hub、扫描、条件启用等机制）：

1. **技能元数据标准化**：名称/版本/类别/平台限制/依赖工具集/需要的配置项/安全等级。  
2. **技能生命周期**：  
   - 创建（agent 自生成或人工编写）  
   - 验证（最小可验证脚本/检查点）  
   - 发布（内部 registry 或 Git-based）  
   - 升级/回滚（锁文件、变更日志）  
3. **安全扫描与信任分级**：  
   - 对脚本/命令/网络访问做静态扫描  
   - 来源标记（官方/团队/第三方）  
   - 强制审阅（高风险技能安装必须人工确认）  
4. **渐进式加载（Progressive Disclosure）**：技能列表只注入索引，真正使用时再加载全文与参考文件，以控制 token 成本与缓存稳定性。  
5. **技能的“可观测性”**：执行次数、成功率、平均耗时、常见失败原因、依赖工具可用性等。

**风险点**：

- 技能过度依赖具体工具/环境，导致可移植性差；需定义“能力层接口”（例如文件操作能力、web 检索能力）  
- agent 自生成技能质量参差；需要“自动测试 + 人工抽检”机制  

**指标**：

- Top N 高频技能的成功率、耗时、失败原因可统计  
- 技能回滚能在分钟级完成  
- 供应链风险事件（高危技能误装/恶意脚本）可被扫描阻断

---

### 方向 4：记忆体系升级：bounded memory + session search + external memory providers 的统一路由

**核心问题**：长期 Agent 一定会“忘记”或“记错”。单纯扩大上下文会导致成本飙升与提示污染；必须系统化。

**工程抓手**（参考 Hermes 的 bounded memory 与 session search、外部 provider）：

1. **保持 bounded memory 的强约束**：把“必须每次都在上下文里”的信息控制在固定 token 预算内（例如 1k~2k tokens），并采用冻结快照注入以提升缓存命中。  
2. **会话搜索成为默认能力**：把“曾经讨论过的细节”放到 FTS/向量检索里，按需检索并摘要注入。  
3. **外部记忆 provider 插件化**：对 KG/实体解析/语义检索提供统一接口；由路由层决定何时调用何种 provider。  
4. **记忆写入策略（Write Policy）**：  
   - 什么时候写 memory？（纠错、偏好、环境事实、项目约定）  
   - 什么时候写 skill？（5+ tool calls 的成功流程、出现 dead-end 后修复的路径）  
   - 什么时候只写 session？（一次性任务细节）  
5. **记忆安全与注入防护**：记忆内容在写入前进行注入/外泄模式扫描（Hermes 的做法是一个很强的信号）。

**风险点**：

- 检索注入会污染 prompt；必须做引用标记与来源分级  
- 多 provider 的一致性与冲突解决复杂；先定义“事实源优先级”  

**指标**：

- “重复问同一个偏好/事实”的频率降低  
- 记忆相关的错误（误记、注入）可被检测与回滚  
- 在不显著增加 token 成本的情况下提升长期任务完成率

---

### 方向 5：上下文工程（Context Engineering）成为平台核心：压缩、缓存、引用、谱系

**核心问题**：随着工具调用增多、对话变长、渠道多样，成本与稳定性会被 prompt 工程主导。

**工程抓手**：

1. **Prompt Assembly 分层**：系统指令、记忆、技能索引、上下文文件、检索结果、当前任务、工具输出——必须有清晰分层与优先级。  
2. **压缩策略（Compression）**：  
   - 触发条件（token 超阈值、时间窗口、工具输出过长）  
   - 压缩粒度（对话段落、工具输出摘要、引用转存）  
   - 谱系记录（压缩前后可追溯、可回放）  
3. **Prompt Caching**：  
   - 冻结快照注入（memory/context files）  
   - 稳定前缀（减少频繁变化的随机信息）  
   - 对检索结果采用引用 ID 而非全文重复注入  
4. **Context References**：建立“引用机制”（类似文献引用）把长材料存入外部存储，仅在需要时按片段引用，减少 token 占用。  

**风险点**：

- 压缩容易丢失关键约束；需要针对“任务约束、权限、失败教训”的保留规则  
- 缓存与动态信息冲突；需要定义哪些信息可冻结、哪些必须实时  

**指标**：

- 平均 token 成本下降/稳定  
- 长会话任务成功率不随轮次显著下降  
- 压缩后回归错误率可被监控

---

### 方向 6：可观测性与调试：从日志 → 结构化轨迹（Trajectory）与可回放（Replay）

**核心问题**：Agent 的 bug 不是“崩溃”，而是“做错事”。必须能复现决策链与工具链。

**工程抓手**：

1. **统一事件模型**：每次 LLM 调用、工具调用、审批、检索、压缩、记忆写入、技能加载都产生结构化事件。  
2. **轨迹存储格式**：兼容训练数据（SFT/RL）与 debug（可回放）。  
3. **回放系统（Replay Harness）**：在固定模型版本/固定工具模拟下回放轨迹，验证策略与提示变更不会破坏关键能力。  
4. **面向人类的 Trace Viewer**：按时间轴展示“输入→推理→工具→输出→状态变更”，支持一键定位失败原因。  

**风险点**：

- 采集过多信息引发隐私风险；需要脱敏与分级存储  
- 回放需要工具模拟；先从核心高频工具做模拟层  

**指标**：

- 关键事故可在小时级复现  
- 回归测试覆盖率（按轨迹/技能）逐步提升  
- 新策略上线后事故率下降

---

### 方向 7：安全治理升级：从“默认安全”到“可证明的安全边界”

OpenClaw Vision 把安全与安全默认放在第一优先级；Hermes 的记忆扫描、技能扫描、审批等机制表明“安全治理”正在工程化。

**工程抓手**：

1. **不可信输入隔离**：对来自未知 DM/群聊/外部网页内容设定不同的工具权限与策略（OpenClaw 的 DM pairing 是代表性做法）。  
2. **审批与授权最小化**：  
   - “读/写/执行/联网/凭证”按能力拆分授权  
   - 高风险操作必须显式确认  
3. **供应链安全**：技能/插件安装需扫描、锁定、审计；敏感配置与密钥不得在消息通道请求输入。  
4. **Prompt 注入与数据外泄防护**：记忆、技能、检索结果与网页内容都可能成为注入载体；需要统一检测与隔离。  

**指标**：

- 未知来源输入触发高权限工具的比例接近 0  
- 外泄/注入事件可被检测拦截  
- 安全策略变更可被审计与回滚

---

### 方向 8：多前端一致性：CLI/IM/语音/Canvas/IDE 的同一语义层

两套系统都把“多入口”当成核心能力：OpenClaw 支持大量消息通道与 Canvas；Hermes 强调 CLI 与 gateway 一致的 slash commands 与交互语义，并加入 ACP（IDE）适配。

**工程抓手**：

1. **统一命令语义**：`/new /reset /stop /retry /undo /model /skills /usage` 等跨入口一致。  
2. **统一 streaming 与 chunking 策略**：不同通道限制不同，但应由同一策略层决定分块与节流，而不是每个 adapter 各写一套。  
3. **“打断-重定向”机制**：用户在工具执行中插入新指令，系统应能在下一个模型边界注入（OpenClaw 的 steer queue、Hermes 的 interrupt 是同一方向）。  
4. **IDE/编辑器集成作为高价值入口**：ACP/类似协议会在半年内加速普及，尤其在代码与文档场景。  

**指标**：

- 新增一个入口（新 IM 平台/IDE）不需要重写核心交互语义  
- 用户跨入口切换时“状态一致性”更好（同一 session、同一记忆、同一技能索引）

---

### 方向 9：从单 Agent → “可控的并行与委派”（Subagents/Workstreams）

Hermes 强调可 spawn 隔离 subagents 并行工作；OpenClaw Vision 反而明确“暂不把 manager-of-managers 作为默认架构”。这并不矛盾：未来半年更可能出现的是：

- **并行作为执行策略**（为吞吐与效率），而不是复杂的“层级组织结构”默认化。  
- 更强调**隔离**（上下文隔离、工作区隔离、权限隔离）与**合并策略**（结果汇总、冲突消解），而不是“无限递归的 Agent 树”。  

**工程抓手**：

1. **Workstream 模型**：把子任务封装成可追踪的 workstream（目标、输入、约束、工具权限、输出格式）。  
2. **隔离边界**：每个 subagent 有独立上下文与可选独立工作区/执行后端。  
3. **合并与审阅**：主 agent 负责汇总、去重、冲突检测、引用标记。  

**指标**：

- 多步骤任务的总体耗时下降  
- 并行不会显著增加错误率（得益于隔离与合并策略）

---

### 方向 10：模型层的“可路由化”与“质量护栏”：Provider failover、模式选择、非 agentic 警告

两套系统都在强化“多模型/多 provider”能力（OpenClaw 有 model failover 文档；Hermes 有 provider runtime resolution 子系统，并在 release notes 中提到非 agentic 模型警告等）。未来半年会继续出现：

- **按任务路由模型**（规划/执行/检索摘要/工具选择/安全检查使用不同模型）  
- **失败自动降级**（provider 失败、速率限制时 fallback）  
- **质量护栏**：对不适合 tool use 的模型给出警告或限制能力  

**工程抓手**：

1. **模型能力画像**：tool-use 适配性、成本、延迟、上下文长度、可靠性。  
2. **路由策略**：主模型 + 辅助模型（摘要、检索、评审、安全扫描）。  
3. **离线评测**：用轨迹回放测试不同模型组合。  

**指标**：

- 成本/成功率/延迟三者的 Pareto 改善  
- 供应商故障时服务可用性提升

---

## 4. 未来半年路线图（建议按 3 个里程碑推进）

下面给出一个以“工程落地”为核心的 6 个月路线图。可根据团队规模压缩或拆分。

### 里程碑 M1（第 1-2 个月）：把系统做“稳”——控制平面与观测闭环打底

1. 建立 **统一事件模型** 与基本 trace（LLM call / tool call / approval / compression / memory write / skill load）。  
2. 梳理并固化 **最小控制平面资源模型**（session、workspace、toolset、skill、identity）。  
3. 实施 **基础安全策略**：不可信输入默认低权限 + 高风险审批门；技能/记忆写入扫描（至少规则级）。  
4. 完成 5-10 条核心任务的 **轨迹采集与回放雏形**（手工回放即可）。  

**交付物**：trace viewer v0、策略引擎 v0、控制平面 schema v0、回放 harness v0。

### 里程碑 M2（第 3-4 个月）：把系统做“强”——执行平面可替换 + 技能工程化

1. 引入 **可插拔执行后端**（至少：本地 + 容器或 SSH 二选一）并统一可观测输出结构。  
2. 将技能升级为“能力包”：元数据、版本、验证步骤、安全扫描、来源审计；支持渐进加载。  
3. 记忆体系升级：bounded memory + session search 形成统一路由（先不引入复杂 KG，也要把接口留好）。  
4. 并行/委派能力：最小可用的 workstream + 隔离边界 + 合并策略。  

**交付物**：execution backend adapter、skills registry/lock、memory router、workstream runner。

### 里程碑 M3（第 5-6 个月）：把系统做“可进化”——评测/数据与自动改进机制上线

1. 完整的 **轨迹格式**（可用于训练/评测）与自动采集；支持脱敏与采样。  
2. 建立 **评测基准集**：覆盖高频技能、关键安全策略、跨通道交互、长会话压缩等。  
3. “学习闭环”第一步：  
   - 自动总结失败案例并生成“修复建议 skill patch”  
   - 高频任务自动生成技能草案并进入人工 review 流程  
4. 与协议生态对接：优先 MCP/ACP 之一做“高质量集成样板”，形成可复用适配层。  

**交付物**：benchmark suite、trajectory pipeline、skill auto-draft、协议适配层。

---

## 5. 架构落地建议：一张“参考架构图”（文字版）

为了便于团队对齐，给出一份文字版参考架构（可直接转成图）。

```
                   ┌────────────────────────────────────┐
                   │            Frontends                │
                   │ CLI / IM Gateway / Voice / Canvas / │
                   │ IDE(ACP)                            │
                   └───────────────┬────────────────────┘
                                   │ unified interaction semantics
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                        CONTROL PLANE                              │
│ Sessions · Identities · Channels · Toolsets · Skills · Jobs(Cron) │
│ Config/State store + audit log + migrations                        │
└───────────────┬───────────────────────────┬───────────────────────┘
                │                           │
                │                           │
                ▼                           ▼
┌───────────────────────────┐     ┌───────────────────────────────┐
│        AGENT CORE          │     │     GOVERNANCE PLANE          │
│ Prompt assembly            │     │ Policy engine (approvals)     │
│ Context compression/caching│     │ Safety scanners (memory/skills)│
│ Tool orchestration         │     │ Observability/Tracing          │
│ Model routing/failover     │     │ Replay & Benchmarks            │
└───────────────┬───────────┘     └───────────────┬───────────────┘
                │                                 │
                ▼                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                        EXECUTION PLANE                            │
│ Tool runtime (registry/dispatch)                                  │
│ Backends: local · container · ssh · serverless                     │
│ Sandboxing: fs/network/process quotas                              │
└───────────────┬──────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE PLANE                                │
│ Bounded memory (MEMORY/USER) + Session store/search + External mem │
│ Skills (packages) + References (context files, artifacts)          │
└──────────────────────────────────────────────────────────────────┘
```

这张图的关键是：**把“学习/记忆/技能”从 agent loop 里拆出来，变成独立平面；把“安全/观测/评测”从产品功能变成治理平面。**

---

## 6. 风险清单（未来半年最可能踩坑的地方）

1. **把“更多工具”误当成“更强智能体”**：工具越多，治理成本越高；没有策略与观测，事故概率按工具数量上升。  
2. **记忆污染与注入**：长期 Agent 最致命的问题不是忘记，而是“记错/被写入恶意指令”。必须把写入扫描与回滚当成默认能力。  
3. **执行后端碎片化**：多 backend 很诱人，但如果没有统一接口与观测，调试成本会爆炸。  
4. **技能生态失控**：没有 lock/审计/信任分级，技能 hub 会变成供应链漏洞入口。  
5. **上下文成本失控**：没有压缩与缓存，随着会话变长成本线性上升且稳定性下降。  
6. **评测缺失导致“越改越退步”**：Agent 改动（prompt、策略、模型、技能）会产生非线性影响，必须依赖轨迹回放与 benchmark。  

---

## 7. 建议的团队分工（按能力域拆分）

为了在 6 个月内跑出结果，建议按能力域组织，而不是按“渠道/工具”分散。

1. **控制平面与网关组**：sessions/channels/identity/jobs/config/migrations  
2. **执行平面组**：tools runtime/backends/sandbox/policy enforcement  
3. **上下文与记忆组**：prompt assembly/compression/caching/memory router/search  
4. **技能工程与生态组**：skills packaging/hub/scan/CI/release  
5. **观测与评测组**：tracing/trajectory/replay/benchmarks/quality gates  
6. **客户端/入口组**：CLI/IM adapters/voice/IDE integration 统一语义  

---

## 8. 附：从 OpenClaw → Hermes 的“演进启示”总结成一句话

> **OpenClaw 把 Agent 做成“本地可控的控制平面”；Hermes 则进一步把它做成“可迁移、可学习、可评测、可进化的长期运行系统”。**  
未来半年，智能体平台的分水岭不是“谁能调用更多工具”，而是“谁能把上述四个平面做成稳定的工程系统”。

---

## 参考链接（用于溯源阅读）

- OpenClaw README（多通道、Gateway、工具与平台概览）：https://raw.githubusercontent.com/openclaw/openclaw/main/README.md  
- OpenClaw Vision（安全默认、插件/技能/MCP 策略、项目方向）：https://raw.githubusercontent.com/openclaw/openclaw/main/VISION.md  
- OpenClaw Agent Runtime（workspace/bootstrap files/skills/sessions/streaming）：https://docs.openclaw.ai/concepts/agent  
- Hermes Agent README（学习闭环、多后端、迁移、自我改进等）：https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md  
- Hermes 架构地图（子系统划分、核心文件与目录）：https://hermes-agent.nousresearch.com/docs/developer-guide/architecture  
- Hermes Persistent Memory（bounded memory + session search + external providers）：https://hermes-agent.nousresearch.com/docs/user-guide/features/memory  
- Hermes Skills System（progressive disclosure、hub、安全扫描、skill_manage）：https://hermes-agent.nousresearch.com/docs/user-guide/features/skills  

