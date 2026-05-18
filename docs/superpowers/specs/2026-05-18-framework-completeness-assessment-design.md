# Framework Completeness Assessment Design

## Motivation

评估 ARF 框架的完成程度，目标：

- **Agent 自管理**：Agent 能在运行时管理自身配置（模型切换、工具加载、记忆管理、Hook 生命周期）
- **Agent 自进化**：Agent 能在对话中生成、校验、注册新资源（Tool/Skill/Model），使之成为永久能力
- **80-90% 用户任务**：用户侧任务可通过新增/编排框架内资源完成，不修改 `src/arf/` 源码

## 评估边界

**不算修改框架**（用户/Agent 行为）：
- 在 workspace 的 `tools/`、`skills/`、`models/` 下新增 YAML + Python 文件
- 通过 Agent 对话触发 resource_scaffold + file_writer 生成新资源
- 通过 file_deleter 删除用户资源
- 通过 manage_hooks 添加/修改 Hook

**算框架维护者工作**（非用户行为）：
- 修改 `src/arf/resources/system/` 下的系统资源定义
- 修改系统 skill 的 prompt_template

**算修改框架源码**（需避免）：
- 修改 `src/arf/` 下任何 Python 代码
- 修改 `pyproject.toml` 增加新依赖

**优先级分类**：
- 高优先级：文件操作（A）、信息获取（B）、资源创建（C）、数据分析（D）、对话增强（G）
- 低优先级（本期不评估）：外部集成（E）、多媒体处理（F）

---

## Part 1: 能力矩阵

每项状态分 4 级：

| 标记 | 含义 |
|------|------|
| ✅ | 完整实现 — 有 YAML 声明 + function.py 实现 + 测试覆盖 + Agent 可触发 |
| ⚠️ | 部分实现 — 有声明但无实现 / 有实现但无测试 / 路径不完整 |
| ❌ | 缺失 — 框架不支持此能力 |
| 🔧 | 框架缺口 — 必须修改 `src/arf/` 源码才能实现 |

### 维度 1：资源 CRUD（自进化核心路径）

每种资源类型覆盖 6 个生命周期操作。

#### 1.1 Tool CRUD

| 操作 | 系统能力 | 状态 |
|------|---------|------|
| Scaffold | resource_scaffold skill（生成 tool.yaml + function.py 骨架） | |
| Validate | validate_tool skill（校验生成结果的格式与完整性） | |
| Write | file_writer 将生成内容写入 `tools/<name>/` | |
| Register | resource_registrar tool 热注册；resource_loader 后加载可用 | |
| Update | file_reader 读 + file_writer 写 + 热加载感知 | |
| Delete | file_deleter 删除目录 + 热加载移除 | |
| Dependency check | resource_registrar + check_deps() 验证依赖模型是否已配置 | |
| Discovery | resource_loader 发现 workspace 中已有资源并加载 | |

#### 1.2 Skill CRUD

| 操作 | 系统能力 | 状态 |
|------|---------|------|
| Scaffold | resource_scaffold skill（生成 skill.yaml） | |
| Validate | validate_tool skill 校验 | |
| Write | file_writer 写入 `skills/<name>/skill.yaml` | |
| Register | resource_registrar + resource_loader | |
| Update | file_reader + file_writer + 热加载 | |
| Delete | file_deleter + 热加载移除 | |

#### 1.3 Model CRUD

| 操作 | 系统能力 | 状态 |
|------|---------|------|
| Scaffold | resource_scaffold 是否支持 model 类型？ | |
| Validate | 是否有 model 配置校验？ | |
| Write | file_writer 写入 `models/<name>/config.yaml` | |
| Register | resource_registrar；model_manager tool | |
| Update | 修改 config.yaml + 热加载 | |
| Delete | file_deleter | |

#### 1.4 Hook CRUD

| 操作 | 系统能力 | 状态 |
|------|---------|------|
| Create | manage_hooks tool → add_hook() | |
| Read | manage_hooks tool → list_hooks() | |
| Update | manage_hooks tool → update_hook() | |
| Delete | manage_hooks tool → remove_hook() | |
| Trigger | HookRunner.run() 在 6 个生命周期事件自动触发 | |

### 维度 2：Agent 运行时自治

| 能力 | 实现路径 | 状态 |
|------|---------|------|
| 模型自动路由 | Classifier → quick_thinking / deep_thinking 自动判定 | |
| 显式模型切换 | 用户说"切换到深度思考" → router._requests_model_change() | |
| 短期记忆管理 | session.md 自动维护，memory_extract skill 提取 | |
| 长期记忆管理 | long_term.md 持久化，memory_store tool 写入 | |
| 记忆压缩 | memory_compress skill → 窗口 75% 时触发 compaction | |
| 渐进式工具加载 | Kernel 工具始终加载 + resource_loader 按需加载 | |
| 上下文窗口管理 | 自动 sliding-window + summary compaction | |
| 错误恢复 | error_handler skill → recovery node → 重试/降级 | |
| Hook 编排 | HookRunner 子进程执行，6 事件（SessionStart~SessionEnd） | |
| 热加载 | ResourceRegistry.reload_user() + file watcher | |
| 依赖检查 | check_deps() 验证资源依赖是否满足 | |
| 双 Agent 协作 | Dispatcher → UserAgent handoff → SysAgent | |

### 维度 3：用户任务类别覆盖

#### A — 文件操作

| 子能力 | 对应工具/技能 | 状态 |
|--------|-------------|------|
| 读取文件 | file_reader | |
| 写入文件 | file_writer（含路径限制） | |
| 删除文件 | file_deleter（含路径限制） | |
| 文件下载 | file_download | |
| 批量文件处理 | 需编排 file_reader + file_writer | |

#### B — 信息获取

| 子能力 | 对应工具/技能 | 状态 |
|--------|-------------|------|
| 网页抓取 | web_fetch | |
| 网页搜索 | web_search | |
| RAG 检索 | rag_operator skill | |
| 数据库查询 | db_operator skill | |

#### C — 资源创建（自进化）

| 子能力 | 对应工具/技能 | 状态 |
|--------|-------------|------|
| 生成新 Tool | tool_generator → resource_scaffold | |
| 生成新 Skill | skill_generator → resource_scaffold | |
| 生成新 Model 配置 | resource_scaffold 是否支持 model 类型？ | |
| 校验生成的资源 | validate_tool | |
| 激活/注册资源 | resource_registrar + resource_loader | |
| 克隆系统资源 | 是否有 `arf clone` / 复制能力？ | |

#### D — 数据分析

| 子能力 | 对应工具/技能 | 状态 |
|--------|-------------|------|
| SQL 查询 | db_operator skill | |
| 数据格式转换 | 需编排 file_reader + Tool | |
| 报表/图表生成 | 需用户 Tool/Skill 支持 | |
| 日志分析 | 需编排 web_fetch / file_reader + deep_thinking | |

#### G — 对话增强

| 子能力 | 对应工具/技能 | 状态 |
|--------|-------------|------|
| 短期记忆 | session.md + memory_extract | |
| 长期记忆 | long_term.md + memory_store | |
| 记忆压缩 | memory_compress skill | |
| 上下文保留 | sliding-window compaction | |
| 模型配置 | model_configurator + model_manager | |

### 维度 4：跨切面关注点

| 关注点 | 检查项 | 状态 |
|--------|--------|------|
| 热加载 | 文件变化检测 → reload_user() → 变更列表 | |
| 热加载 | 新增工具后 Agent 可在同 session 内使用 | |
| 可观测性 | SQLite trace 数据库（6 表） | |
| 可观测性 | TraceView 瀑布图 | |
| 流式输出 | SSE streaming + WebSocket | |
| 流式输出 | handoff 事件正确发射(frontend 过渡标记) | |
| 前端 | 8 视图 + 13 组件 | |
| 前端 | 资源管理 UI（查看/编辑/删除用户资源） | |
| 双 Agent | handoff 上下文字段完整传递 | |
| 双 Agent | Phase 1 turns 消耗正确传导到 Phase 2 | |
| 测试 | 单元测试覆盖 | |
| 测试 | 集成测试覆盖（handoff 完整流程） | |
| 测试 | 边界场景覆盖 | |
| 安全 | UserAgent file_writer/file_deleter 路径限制生效 | |
| 安全 | 用户资源不能覆盖系统资源（冲突检测） | |
| 安全 | Hook 管理 JWT 认证保护 | |

---

## Part 2: 测试场景集

使用真实用户话术，优先验证意图翻译 + 核心路径。

### 第 1 组：资源自进化

每场景先验证 UserAgent 的意图翻译是否正确，再验证后续流程。

| # | 用户实际说的话 | 意图翻译验证 | 完整流程 |
|---|-------------|-------------|---------|
| S1 | "我想要个能查汇率的，输入币种和金额就行" | UserAgent 识别为"新建 tool"→ `currency_converter`，参数 `from_currency, to_currency, amount`。若信息不足（币种范围？数据源？），应追问 1-2 个问题而非直接创建 | handoff → SysAgent 调 resource_scaffold 生成 tool.yaml + function.py → file_writer 写入 → validate_tool 校验 → resource_registrar 注册 → 同 session 内工具可用 |
| S2 | "能不能帮我弄个东西，让我每天下班前能自动看看今天干了啥" | UserAgent 识别为"新建 skill"（非单次工具，需编排 file_reader + deep_thinking）→ `daily_summary` skill | handoff → skill_generator → resource_scaffold 生成 skill.yaml → file_writer 写入 → 校验 → 注册 |
| S3 | "之前那个汇率的东西，能不能加个功能，让它也支持查过去某一天的汇率" | UserAgent 识别为"修改已有工具"（追加参数 `date`）+ 需 handoff（路径可能在 tools/ 内，UserAgent 受限） | handoff → file_reader 读 tool.yaml → 追加 `date` 参数 → file_writer 写回 → 热加载生效 |
| S4 | "汇率那个工具我不要了，帮我删了吧" | UserAgent 识别为"删除已有工具"。需确认是用户 workspace 内的工具（非系统），路径在 `tools/` → UserAgent 受限需 handoff | handoff → file_deleter 删除工具目录 → 热加载移除 |
| S5 | "我手动在 tools/ 下放了个东西，你帮我看看能不能用" | UserAgent 识别为"发现已有资源" → resource_loader | resource_loader 加载 → config_default.yaml / tool.yaml 校验 → 告知用户可用性 |
| S6 | "帮我把 web_fetch 那个系统工具复制一份出来，我想改改它默认的 timeout" | UserAgent 识别为"克隆系统资源到用户空间" | 是否存在 `arf clone` 机制？若无，需 file_reader 读 → file_writer 写入用户空间 → resource_registrar 注册 |

### 第 2 组：Agent 运行时自治

| # | 用户实际说的话 | 验证点 |
|---|-------------|--------|
| S7 | "今天天气怎么样"（简单问题） | Classifier 判 simple → quick_thinking 响应，不触发模型切换 |
| S8 | "帮我设计一个支持多租户的 SaaS 权限系统，要包含 RBAC 和 ABAC" | Classifier 判 complex → 自动切换 deep_thinking |
| S9 | "用深度思考模式，分析下这个文件" | 显式切换 → router 触发 classify → deep_thinking 响应 |
| S10 | 模拟 30+ 轮长对话，逐轮追加信息，直到接近上下文窗口上限 | 触发 compaction → 旧轮次压缩为摘要 → 继续对话不丢关键信息 |
| S11 | 模拟多轮对话中建立偏好："以后回复都用中文"→ 开新 session → "你还记得我之前说过用什么语言吗" | Session 内 memory_extract 写入 → long_term.md 持久化 → 新 session 从 long_term_memory section 加载 |

### 第 3 组：用户任务类别覆盖

| # | 用户实际说的话 | 类别 | 验证点 |
|---|-------------|------|--------|
| S12 | "把这个 PDF 文件读一下，把里面的表格数据整理成 CSV" | A | file_reader（如有 PDF 能力）→ 提取结构化数据 → file_writer 写 CSV → file_download |
| S13 | "帮我搜一下最近关于 LangGraph 的新闻，挑出和 multi-agent 相关的" | B | web_search → web_fetch（逐条）→ 过滤整合 |
| S14 | "查一下数据库里最近 7 天的 session 数量，按模型分类汇总" | D | db_operator skill → SQL 查询 → 汇总 |
| S15 | "把这三个 Excel 文件读出来，合并去重，找出共同的客户 ID" | A+D | file_reader（xlsx）→ 数据合并 → file_writer 输出 |
| S16 | "帮我把这个长对话里关于部署流程的部分提取出来，整理成一份 checklist" | G | memory_extract skill → deep_thinking 总结 → file_writer |

### 第 4 组：边界与压力

| # | 用户实际说的话 | 验证点 |
|---|-------------|--------|
| S17 | "帮我装一个能发微信消息的工具" | 涉及外部 API 集成（低优先级）。Agent 告知需先配置对应 model（API endpoint），无法凭空创建需要外部依赖的工具 |
| S18 | 人为使工具 function.py 运行时抛异常 | error_handler skill 介入 → 友好报错（包含 exception type + detail）→ 不中断对话 |
| S19 | 在 SysAgent Phase 2 处理复杂任务时耗尽 max_turns | 明确提示："当前任务复杂度较高，已处理 N 轮，建议开新 session 继续" |
| S20 | "帮我创建一个依赖 vision 模型的工具"，但 vision 模型未配置 | SysAgent 调 check_deps() → 发现 missing → 提示用户先配置 vision 模型 |

### 第 5 组：跨切面

| # | 场景 | 验证点 |
|---|------|--------|
| S21 | S1 完整流程在前端 Web UI 中执行 | 流式输出正常 → handoff 过渡标记出现 → Sys Agent 结果正确呈现 |
| S22 | S1 流程的 trace 在 TraceView 中查看 | SQLite trace 记录完整 → 瀑布图展示各阶段耗时（classify → call_model → execute_tools → respond） |
| S23 | 并发：同时开两个 session，一个做资源创建，一个做普通对话 | 互不干扰 → 各自 session 独立 |

---

## Part 3: 差距分析框架

完成 Part 1 和 Part 2 的评估后，按以下结构输出差距报告：

### 3.1 阻断性缺口

导致 Agent 自进化闭环无法走通的缺陷。例如：
- resource_scaffold 不支持 model 类型生成 → 无法自建 model
- file_writer 路径限制错误阻止合法操作
- 热加载不生效 → 新资源需重启才能用

### 3.2 功能性缺口

闭合链路可走通，但体验或覆盖不足。例如：
- 某系统工具声明了 tool.yaml 但无 function.py → 前端显示但不可用
- 缺少特定用户任务类别所需的通用能力（如无 Excel 解析工具）
- 错误信息对用户不友好

### 3.3 架构性缺口

当前架构设计上不支持、需扩展框架才能实现的场景。例如：
- 用户资源无法声明对系统资源的依赖关系
- Hook 无法在 PreToolUse 中修改 tool input
- 资源版本管理（升级/回滚）完全缺失

### 3.4 测试覆盖缺口

测试不足导致回归风险高的区域。

### 输出格式

每个缺口记录：

```
## [阻断/功能/架构/测试] <简短标题>
**影响范围**：哪类用户任务受影响
**当前表现**：实际观察到的行为
**期望行为**：应该怎样
**修复方向**：建议的修复路径（改框架 / 补资源 / 加测试）
**优先级**：P0（阻断）/ P1（高）/ P2（中）/ P3（低）
```

---

## 执行方法

### 阶段 1：快速扫描（30 min）

- 遍历 `src/arf/resources/system/` 下所有工具/技能目录
- 标记每个资源的：`[有 tool.yaml/skill.yaml]` `[有 function.py]` `[有 config_default.yaml]` `[有测试]`
- 产出：资源清单 CSV + `✅/⚠️/❌` 初步状态

### 阶段 2：能力矩阵填充（2 hr）

- 基于阶段 1 的清单，填充 Part 1 全部 4 个维度的能力矩阵
- 对每个 `⚠️` 项判断是否影响核心路径

### 阶段 3：实证测试（3 hr）

- 按 Part 2 场景清单逐条执行
- 记录每次都：实际结果、是否触发框架源码修改、异常信息
- 优先跑第 1 组（自进化）和第 4 组（边界）

### 阶段 4：差距分析与报告（1 hr）

- 按 Part 3 框架整理缺口
- 输出完整评估报告

### 判定标准

**自进化闭环达标**：S1-S5 全部通过，即用户在对话中说出模糊需求 → Agent 正确翻译意图 → 生成 → 校验 → 注册 → 立即可用，全程不改 `src/arf/`。

**自管理闭环达标**：S7-S11 全部通过，Agent 在运行时能自主完成模型切换、记忆管理、上下文压缩、Hook 管理。

**80-90% 阈值判定**：Part 2 的所有高优先级场景（S1-S16 + S20），通过率 ≥ 80%，且所有阻断性缺口已修复。

---

*评估结果将写入独立报告文件，本设计文档仅定义方法论。*
